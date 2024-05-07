"""
Define core Sim classes
"""

import itertools
import numpy as np
import sciris as sc
import starsim as ss
import matplotlib.pyplot as pl

__all__ = ['Sim', 'AlreadyRunError', 'demo', 'diff_sims', 'check_sims_match']


class Sim(sc.prettyobj):
    """
    The Sim object
    
    All Starsim simulations run via the Sim class. It is responsible for initializing
    and running all modules and generating results.
    """
    def __init__(self, pars=None, label=None, people=None, demographics=None, diseases=None, networks=None,
                 interventions=None, analyzers=None, connectors=None, copy_inputs=True, **kwargs):
        # Make default parameters (using values from parameters.py)
        self.pars = ss.make_pars() # Start with default pars
        args = dict(label=label, people=people, demographics=demographics, diseases=diseases, networks=networks, 
                    interventions=interventions, analyzers=analyzers, connectors=connectors)
        args = {key:val for key,val in args.items() if val is not None} # Remove None inputs
        pars = sc.mergedicts(pars, args, kwargs, _copy=copy_inputs)
        self.pars.update(pars)  # Update the parameters
        
        # Set attributes
        self.label = label # Usually overwritten during initalization by the parameters
        self.created = sc.now()  # The datetime the sim was created
        self.version = ss.__version__ # The Starsim version
        self.gitinfo = sc.gitinfo(path=__file__, verbose=False)
        self.initialized = False  # Whether initialization is complete
        self.complete = False  # Whether a simulation has completed running
        self.results_ready = False  # Whether results are ready
        self.dists = ss.Dists(obj=self) # Initialize the random number generator container
        self.results = ss.Results(module='sim')  # For storing results
        self.elapsed = None # The time required to run
        self.summary = None  # For storing a summary of the results
        self.filename = None # Store the filename, if saved
        return
    
    def __getitem__(self, key):
        """ Allow dict-like access, e.g. sim['diseases'] """
        return getattr(self, key)
    
    def __setitem__(self, key, value):
        """ Allow dict-like access, e.g. sim['created'] = sc.now() """
        return setattr(self, key, value)
    
    def initialize(self, reset=False, **kwargs):
        """ Perform all initializations for the sim; most heavy lifting is done by the parameters """
        # Validation and initialization
        ss.set_seed(self.pars.rand_seed) # Reset the seed before the population is created -- shouldn't matter if only using Dist objects
        p = self.pars.initialize(sim=self, reset=reset, **kwargs) # Initialize the parameters, including people and modules
        
        # Move initialized modules to the sim
        keys = ['label', 'demographics', 'networks', 'diseases', 'interventions', 'analyzers']
        for key in keys:
            setattr(self, key, p.pop(key))
            
        # Initialize all the modules with the sim
        modmap = ss.module_map()
        for modkey in modmap.keys():
            modlist = self[modkey]
            for mod in modlist.values():
                mod.initialize(self)
                
        # Initialize products # TODO: think about simplifying
        for intv in self.interventions:
            if intv.product:
                intv.product.initialize(self)
        
        # Initialize all distributions now that everything else is in place
        self.dists.initialize(obj=self, base_seed=p.rand_seed, force=True)

        # Final steps
        self.initialized = True
        self.complete = False
        self.results_ready = False
        return self

    @property
    def modules(self):
        """ Return iterator over all Module instances (stored in standard places) in the Sim """
        return itertools.chain(
            self.demographics(),
            self.networks(),
            self.diseases(),
            self.interventions(),
            [intv.product for intv in self.interventions() if isinstance(intv.product, ss.Product)],
            self.analyzers(),
        )
    
    @property
    def year(self): # TODO: remove when we do the time refactor
        return self.yearvec[self.ti]

    def step(self):
        """ Step through time and update values """

        # Set the time and if we have reached the end of the simulation, then do nothing
        if self.complete:
            errormsg = 'Simulation already complete (call sim.initialize() to re-run)'
            raise AlreadyRunError(errormsg)

        # Advance random number generators forward to prepare for any random number calls that may be necessary on this step
        self.dists.jump(to=self.ti+1)  # +1 offset because ti=0 is used on initialization

        # Update demographic modules (create new agents from births/immigration, schedule non-disease deaths and emigration)
        for dem in self.demographics():
            dem.step()

        # Carry out autonomous state changes in the disease modules. This allows autonomous state changes/initializations
        # to be applied to newly created agents
        for disease in self.diseases():
            disease.step()

        # Update networks - this takes place here in case autonomous state changes at this timestep
        # affect eligibility for contacts
        for network in self.networks():
            network.step()

        # Apply interventions - new changes to contacts will be visible and so the final networks can be customized by
        # interventions, by running them at this point
        for intervention in self.interventions():
            intervention()

        # Carry out transmission/new cases
        for disease in self.diseases():
            disease.transmit()

        # Execute deaths that took place this timestep (i.e., changing the `alive` state of the agents). This is executed
        # before analyzers have run so that analyzers are able to inspect and record outcomes for agents that died this timestep
        uids = self.people.resolve_deaths()
        for disease in self.diseases():
            disease.die(uids)

        # Update results
        for disease in self.diseases():
            disease.update_results()

        for analyzer in self.analyzers():
            analyzer()
            
        # Clean up dead agents
        self.people.finish_step()

        # Tidy up
        self.ti += 1
        if self.ti == self.npts:
            self.complete = True
        return

    def run(self, until=None, verbose=None):
        """ Run the model once """

        # Initialization steps
        T = sc.timer()
        if not self.initialized:
            self.initialize()
            self._orig_pars = sc.dcp(self.pars)  # Create a copy of the parameters to restore after the run

        if verbose is None:
            verbose = self.pars.verbose

        # Check for AlreadyRun errors
        errormsg = None
        if until is None: until = self.npts
        if until > self.npts:
            errormsg = f'Requested to run until t={until} but the simulation end is ti={self.npts}'
        if self.ti >= until:  # NB. At the start, self.t is None so this check must occur after initialization
            errormsg = f'Simulation is currently at t={self.ti}, requested to run until ti={until} which has already been reached'
        if self.complete:
            errormsg = 'Simulation is already complete (call sim.initialize() to re-run)'
        if errormsg:
            raise AlreadyRunError(errormsg)

        # Main simulation loop
        while self.ti < until:

            # Check if we were asked to stop
            elapsed = T.toc(output=True)

            # Print progress
            if verbose:
                simlabel = f'"{self.label}": ' if self.label else ''
                string = f'  Running {simlabel}{self.yearvec[self.ti]:0.1f} ({self.ti:2.0f}/{self.npts}) ({elapsed:0.2f} s) '
                if verbose >= 2:
                    sc.heading(string)
                elif verbose > 0:
                    if not (self.ti % int(1.0 / verbose)):
                        sc.progressbar(self.ti + 1, self.npts, label=string, length=20, newline=True)

            # Actually run the model
            self.step()

        # If simulation reached the end, finalize the results
        if self.complete:
            self.elapsed = elapsed
            self.finalize(verbose=verbose)
            sc.printv(f'Run finished after {elapsed:0.2f} s.\n', 1, verbose)

        return self

    def finalize(self, verbose=None):
        """ Compute final results """

        if self.results_ready:
            # Because the results are rescaled in-place, finalizing the sim cannot be run more than once or
            # otherwise the scale factor will be applied multiple times
            raise AlreadyRunError('Simulation has already been finalized')

        # Scale the results
        for reskey, res in self.results.items(): # TODO: does this work for disease-specific results?
            if isinstance(res, ss.Result) and res.scale:
                self.results[reskey] = self.results[reskey] * self.pars.pop_scale

        for module in self.modules:
            module.finalize(self)

        self.summarize()
        self.results_ready = True  # Set this first so self.summary() knows to print the results
        self.ti -= 1  # During the run, this keeps track of the next step; restore this be the final day of the sim
        return

    def summarize(self, how='default'):
        """
        Provide a quick summary of the sim
        
        Args:
            how (str): how to summarize: can be 'mean', 'median', 'last', or a mapping of result keys to those
        
        Returns the last entry for count and cumulative results, and the mean otherwise
        """
        
        def get_func(key, how, default='mean'):
            """
            Find the right function by matching the "how" key with the result key
            
            For example, hkey="cum_ " will match result key "cum_infections"
            """
            func = None
            for hkey,hfunc in how.items():
                if hkey in key:
                    func = hfunc
                    break
            if func is None:
                func = default
            return func
            
        def get_result(res, func):
            """ Convert a string to the actual function to use, e.g. "median" maps to np.median() """
            if   func == 'mean':   return res.mean()
            elif func == 'median': return np.median(res)
            elif func == 'last':   return res[-1]
            elif callable(func):   return func(res)
            else: raise Exception(f'"{func}" is not a valid function')
        
        # Convert "how" from a string to a dict
        if how == 'default':
            how = {'n_':'mean', 'new_':'mean', 'cum_':'last', '':'mean'}
        elif isinstance(how, str):
            how = {'':how} # Match everything
        
        summary = sc.objdict()
        flat = sc.flattendict(self.results, sep='_')
        for key, res in flat.items():
            try:
                func = get_func(key, how)
                entry = get_result(res, func)
            except Exception as E:
                entry = f'N/A {E}'
            summary[key] = entry
        self.summary = summary
        return summary
    
    def disp(self):
        print(self.summary)
        return
    
    def shrink(self, skip_attrs=None, in_place=True):
        """
        "Shrinks" the simulation by removing the people and other memory-intensive
        attributes (e.g., some interventions and analyzers), and returns a copy of
        the "shrunken" simulation. Used to reduce the memory required for RAM or
        for saved files.

        Args:
            skip_attrs (list): a list of attributes to skip (remove) in order to perform the shrinking; default "people"
            in_place (bool): whether to perform the shrinking in place (default), or return a shrunken copy instead

        Returns:
            shrunken (Sim): a Sim object with the listed attributes removed
        """
        # By default, skip people (~90% of memory), popdict, and _orig_pars (which is just a backup)
        if skip_attrs is None:
            skip_attrs = ['people']

        # Create the new object, and copy original dict, skipping the skipped attributes
        if in_place:
            shrunken = self
            for attr in skip_attrs:
                setattr(self, attr, None)
        else:
            shrunken = object.__new__(self.__class__)
            shrunken.__dict__ = {k: (v if k not in skip_attrs else None) for k, v in self.__dict__.items()}

        # Don't return if in place
        if in_place:
            return
        else:
            return shrunken

    def export_df(self):
        """ Export results as a Pandas dataframe """
        if not self.results_ready:  # pragma: no cover
            errormsg = 'Please run the sim before exporting the results'
            raise RuntimeError(errormsg)

        def flatten_results(d, prefix=''):
            flat = {}
            for key, val in d.items():
                if isinstance(val, dict):
                    flat.update(flatten_results(val, prefix=prefix+key+'.'))
                else:
                    flat[prefix+key] = val
            return flat

        resdict = flatten_results(self.results)
        resdict['t'] = self.yearvec
        df = sc.dataframe.from_dict(resdict).set_index('t')
        return df

    def save(self, filename=None, keep_people=None, skip_attrs=None, **kwargs):
        """
        Save to disk as a gzipped pickle.

        Args:
            filename (str or None): the name or path of the file to save to; if None, uses stored
            keep_people (bool or None): whether to keep the people
            skip_attrs (list): attributes to skip saving
            kwargs: passed to sc.makefilepath()

        Returns:
            filename (str): the validated absolute path to the saved file

        **Example**::

            sim.save() # Saves to a .sim file
        """

        # Set keep_people based on whether we're in the middle of a run
        if keep_people is None:
            if self.initialized and not self.results_ready:
                keep_people = True
            else:
                keep_people = False

        # Handle the filename
        if filename is None:
            filename = self.simfile
        filename = sc.makefilepath(filename=filename, **kwargs)
        self.filename = filename  # Store the actual saved filename

        # Handle the shrinkage and save
        if skip_attrs or not keep_people:
            obj = self.shrink(skip_attrs=skip_attrs, in_place=False)
        else:
            obj = self
        sc.save(filename=filename, obj=obj)
        return filename

    @staticmethod
    def load(filename, *args, **kwargs):
        """ Load from disk from a gzipped pickle.  """
        sim = sc.load(filename, *args, **kwargs)
        if not isinstance(sim, Sim):  # pragma: no cover
            errormsg = f'Cannot load object of {type(sim)} as a Sim object'
            raise TypeError(errormsg)
        return sim

    def export_pars(self, filename=None, indent=2, *args, **kwargs):
        '''
        Return parameters for JSON export -- see also to_json().

        This method is required so that interventions can specify
        their JSON-friendly representation.

        Args:
            filename (str): filename to save to; if None, do not save
            indent (int): indent (int): if writing to file, how many indents to use per nested level
            args (list): passed to savejson()
            kwargs (dict): passed to savejson()

        Returns:
            pardict (dict): a dictionary containing all the parameter values
        '''
        pardict = {}
        for key,item in self.pars.items():
            if key in ss.module_map().keys():
                if np.iterable(item):
                    item = [mod.to_json() for mod in item]
                else:
                    try:
                        item = item.to_json()
                    except:
                        pass
            elif key == 'people':
                continue
            pardict[key] = item
        if filename is not None:
            sc.savejson(filename=filename, obj=pardict, indent=indent, *args, **kwargs)
        return pardict

    def to_json(self, filename=None, keys=None, tostring=False, indent=2, verbose=False, *args, **kwargs):
        '''
        Export results and parameters as JSON.

        Args:
            filename (str): if None, return string; else, write to file
            keys (str or list): attributes to write to json (default: results, parameters, and summary)
            tostring (bool): if not writing to file, whether to write to string (alternative is sanitized dictionary)
            indent (int): if writing to file, how many indents to use per nested level
            verbose (bool): detail to print
            args (list): passed to savejson()
            kwargs (dict): passed to savejson()

        Returns:
            A unicode string containing a JSON representation of the results,
            or writes the JSON file to disk

        **Examples**::

            json = sim.to_json()
            sim.to_json('results.json')
            sim.to_json('summary.json', keys='summary')
        '''

        # Handle keys
        if keys is None:
            keys = ['results', 'pars', 'summary', 'short_summary']
        keys = sc.promotetolist(keys)

        # Convert to JSON-compatible format
        d = sc.objdict()
        for key in keys:
            if key == 'results':
                if self.results_ready:
                    resdict = self.export_results(for_json=True) # TODO: not implemented
                    d.results = resdict
                else:
                    d.results = 'Results not available (Sim has not yet been run)'
            elif key in ['pars', 'parameters']:
                pardict = self.export_pars()
                d.parameters = pardict
            elif key == 'summary':
                if self.results_ready:
                    d.summary = dict(sc.dcp(self.summary))
                else:
                    d.summary = 'Summary not available (Sim has not yet been run)'
            elif key == 'short_summary':
                if self.results_ready:
                    d.short_summary = dict(sc.dcp(self.short_summary))
                else:
                    d.short_summary = 'Full summary not available (Sim has not yet been run)'
            else:  # pragma: no cover
                try:
                    d[key] = sc.sanitizejson(getattr(self, key))
                except Exception as E:
                    errormsg = f'Could not convert "{key}" to JSON: {str(E)}; continuing...'
                    print(errormsg)

        if filename is None:
            output = sc.jsonify(d, tostring=tostring, indent=indent, verbose=verbose, *args, **kwargs)
        else:
            output = sc.savejson(filename=filename, obj=d, indent=indent, *args, **kwargs)

        return output

    def plot(self, key=None, **kwargs):
        """
        Plot the results
        
        Args:
            key (str): the disease to plot (if None, plot everything)
            kwargs (dict): passed to ``pl.subplots()``, and ultimately to ``pl.figure()``
            
        **Example**::
            
            sim = ss.Sim(diseases='sir', networks='random').run()
            sim.plot('sir', fig_size=(12,12))
        """
        with sc.options.with_style('fancy'):
            flat = sc.flattendict(self.results, sep=': ')
            yearvec = flat.pop('yearvec')
            if key is not None:
                flat = {k:v for k,v in flat.items() if k.startswith(key)}
            fig, axs = sc.getrowscols(len(flat), make=True, **kwargs)
            for ax, (k, v) in zip(axs.flatten(), flat.items()):
                ax.plot(yearvec, v)
                ax.set_title(k)
                ax.set_xlabel('Year')
        return fig


class AlreadyRunError(RuntimeError):
    """ Raised if trying to re-run an already-run sim without re-initializing """
    pass


def demo(run=True, plot=True, summary=True, show=True, **kwargs):
    """
    Create a simple demo simulation for Starsim
    
    Defaults to using the SIR model with a random network, but these can be configured.
    
    Args:
        run (bool): whether to run the sim
        plot (bool): whether to plot the results
        summary (bool): whether to print a summary of the results
        kwargs (dict): passed to ``ss.Sim()``
    
    **Examples**::
        
        ss.demo() # Run, plot, and show results
        ss.demo(diseases='hiv', networks='mf') # Run with different defaults
    """
    pars = sc.mergedicts(dict(diseases='sir', networks='random'), kwargs)
    sim = Sim(pars)
    if run:
        sc.heading('Running demo:')
        sim.run()
        if summary:
            sc.heading('Results:')
            print(sim.summary)
            if plot:
                sim.plot()
                if show:
                    pl.show()
    return sim


def diff_sims(sim1, sim2, skip_key_diffs=False, skip=None, full=False, output=False, die=False):
    '''
    Compute the difference of the summaries of two simulations, and print any
    values which differ.

    Args:
        sim1 (sim/dict): either a simulation object or the sim.summary dictionary
        sim2 (sim/dict): ditto
        skip_key_diffs (bool): whether to skip keys that don't match between sims
        skip (list): a list of values to skip
        full (bool): whether to print out all values (not just those that differ)
        output (bool): whether to return the output as a string (otherwise print)
        die (bool): whether to raise an exception if the sims don't match
        require_run (bool): require that the simulations have been run

    **Example**::

        s1 = ss.Sim(rand_seed=1).run()
        s2 = ss.Sim(rand_seed=2).run()
        ss.diff_sims(s1, s2)
    '''

    if isinstance(sim1, Sim):
        sim1 = sim1.summarize()
    if isinstance(sim2, Sim):
        sim2 = sim2.summarize()
    for sim in [sim1, sim2]:
        if not isinstance(sim, dict):  # pragma: no cover
            errormsg = f'Cannot compare object of type {type(sim)}, must be a sim or a sim.summary dict'
            raise TypeError(errormsg)

    # Compare keys
    keymatchmsg = ''
    sim1_keys = set(sim1.keys())
    sim2_keys = set(sim2.keys())
    if sim1_keys != sim2_keys and not skip_key_diffs:  # pragma: no cover
        keymatchmsg = "Keys don't match!\n"
        missing = list(sim1_keys - sim2_keys)
        extra = list(sim2_keys - sim1_keys)
        if missing:
            keymatchmsg += f'  Missing sim1 keys: {missing}\ns'
        if extra:
            keymatchmsg += f'  Extra sim2 keys: {extra}\n'

    # Compare values
    valmatchmsg = ''
    mismatches = {}
    n_mismatch = 0
    skip = sc.tolist(skip)
    for key in sim2.keys():  # To ensure order
        if key in sim1_keys and key not in skip:  # If a key is missing, don't count it as a mismatch
            sim1_val = sim1[key] if key in sim1 else 'not present'
            sim2_val = sim2[key] if key in sim2 else 'not present'
            mm = not np.isclose(sim1_val, sim2_val, equal_nan=True)
            n_mismatch += mm
            if mm or full:
                mismatches[key] = {'sim1': sim1_val, 'sim2': sim2_val}

    if len(mismatches):
        valmatchmsg = '\nThe following values differ between the two simulations:\n' if not full else ''
        df = sc.dataframe.from_dict(mismatches).transpose()
        diff = []
        ratio = []
        change = []
        small_change = 1e-3  # Define a small change, e.g. a rounding error
        for mdict in mismatches.values():
            old = mdict['sim1']
            new = mdict['sim2']
            numeric = sc.isnumber(sim1_val) and sc.isnumber(sim2_val)
            if numeric and old > 0:
                this_diff = new - old
                this_ratio = new / old
                abs_ratio = max(this_ratio, 1.0 / this_ratio)

                # Set the character to use
                if abs_ratio < small_change:
                    change_char = '≈'
                elif new > old:
                    change_char = '↑'
                elif new < old:
                    change_char = '↓'
                elif new == old:
                    change_char = '='
                else:
                    errormsg = f'Could not determine relationship between sim1={old} and sim2={new}'
                    raise ValueError(errormsg)

                # Set how many repeats it should have
                repeats = 1
                if abs_ratio == 0:
                    repeats = 0
                if abs_ratio >= 1.1:
                    repeats = 2
                if abs_ratio >= 2:
                    repeats = 3
                if abs_ratio >= 10:
                    repeats = 4

                this_change = change_char * repeats
            else:  # pragma: no cover
                this_diff = np.nan
                this_ratio = np.nan
                this_change = 'N/A'

            diff.append(this_diff)
            ratio.append(this_ratio)
            change.append(this_change)

        df['diff'] = diff
        df['ratio'] = ratio
        for col in ['sim1', 'sim2', 'diff', 'ratio']:
            df[col] = df[col].round(decimals=3)
        df['change'] = change
        valmatchmsg += str(df)

    # Raise an error if mismatches were found
    mismatchmsg = keymatchmsg + valmatchmsg
    if mismatchmsg:  # pragma: no cover
        if die and n_mismatch: # To catch full=True case
            raise ValueError(mismatchmsg)
        elif output:
            return df
        else:
            print(mismatchmsg)
            return True
    else:
        if not output:
            print('Sims match')
        return False


def check_sims_match(*args, full=False):
    """
    Shortcut to using ss.diff_sims() to check if multiple sims match
    
    Args:
        args (list): a list of 2 or more sims to compare
        full (bool): if True, return whether each sim matches the first 
    
    **Example**::
        
        s1 = ss.Sim(diseases='sir', networks='random')
        s2 = ss.Sim(pars=dict(diseases='sir', networks='random'))
        s3 = ss.Sim(diseases=ss.SIR(), networks=ss.RandomNet())
        assert ss.check_sims_match(s1, s2, s3)
    """
    if len(args) < 2:
        errormsg = 'Must compare at least 2 sims'
        raise ValueError(errormsg)
    base = args[0]
    matches = []
    for other in args[1:]:
        diff = diff_sims(base, other, full=False, output=False, die=False)
        matches.append(not(diff)) # Return the opposite of the diff
    if full:
        return matches
    else:
        return all(matches)
        