"""
Define array-handling classes, including agent states
"""

import numpy as np
import starsim as ss

# Shorten these for performance
ss_float = ss.dtypes.float
ss_int   = ss.dtypes.int
ss_bool  = ss.dtypes.bool

__all__ = ['check_dtype', 'Arr', 'FloatArr', 'BoolArr', 'IndexArr', 'uids']


def check_dtype(dtype, default=None):
    """ Check that the supplied dtype is one of the supported options """
    
    # Handle dtype
    if dtype is None:
        if default is None:
            errormsg = 'Must supply either a dtype or a default value'
            raise ValueError(errormsg)
        else:
            dtype = type(default)
    
    if dtype in ['float', float, np.float64, np.float32]:
        dtype = ss_float
    elif dtype in ['int', int, np.int64, np.int32]:
        dtype = ss_int
    elif dtype in ['bool', bool, np.bool_]:
        dtype = ss_bool
    else:
        warnmsg = f'Data type {type(default)} not a supported data type; set warn=False to suppress warning'
        ss.warn(warnmsg)
    
    return dtype


class Arr(np.lib.mixins.NDArrayOperatorsMixin):
    """
    Store a state of the agents (e.g. age, infection status, etc.) as an array.
    
    In practice, ``Arr`` objects can be used interchangeably with NumPy arrays.
    They have two main data interfaces: ``Arr.raw`` contains the "raw", underlying
    NumPy array of the data. ``Arr.values`` contains the "active" values, which
    usually corresponds to agents who are alive.
    
    By default, operations are performed on active agents only (specified by ``Arr.auids``,
    which is a pointer to ``sim.people.auids``). For example, ``sim.people.age.mean()``
    will only use the ages of active agents. Thus, ``sim.people.age.mean()``
    is equal to ``sim.people.age.values.mean()``, not ``sim.people.age.raw.mean()``.
    
    If indexing by an int or slice, ``Arr.values`` is used. If indexing by an
    ``ss.uids`` object, ``Arr.raw`` is used. ``Arr`` objects can't be directly
    indexed by a list or array of ints, as this would be ambiguous about whether
    ``values`` or ``raw`` is intended. For example, if there are 1000 people in a 
    simulation and 100 of them have died, ``sim.people.age[999]`` will return
    an ``IndexError`` (since ``sim.people.age[899]`` is the last active agent),
    whereas ``sim.people.age[ss.uids(999)]`` is valid.

    Args: 
        name (str): The name for the state (also used as the dictionary key, so should not have spaces etc.)
        dtype (class): The dtype to use for this instance (if None, infer from value)
        default (any): Specify default value for new agents. This can be
        - A scalar with the same dtype (or castable to the same dtype) as the State
        - A callable, with a single argument for the number of values to produce
        - A ``ss.Dist`` instance
        nan (any): the value to use to represent NaN (not a number); also used as the default value if not supplied
        raw (arr): if supplied, the raw values to use
        label (str): The human-readable name for the state
        coerce (bool): Whether to ensure the the data is one of the supported data types
        skip_init (bool): Whether to skip initialization with the People object (used for uid and slot states)
    """
    def __init__(self, name, dtype=None, default=None, nan=None, raw=None, label=None, coerce=True, skip_init=False):
        if coerce:
            dtype = check_dtype(dtype, default)
        
        # Set attributes
        self.name = name
        self.label = label or name
        self.default = default
        self.nan = nan
        self.dtype = dtype
        
        # Properties that are initialized later
        self.raw = np.empty(0, dtype=dtype)
        self.people = None
        self.len_used = 0
        self.len_tot = 0
        self.initialized = skip_init
        if raw is not None:
            self.grow(new_uids=uids(np.arange(len(raw))), new_vals=raw)
        return
    
    def __repr__(self):
        arr_str = np.array2string(self.values, max_line_width=200)
        string = f'<{self.__class__.__name__} "{str(self.name)}", len={len(self)}, {arr_str}>'
        return string
    
    def __len__(self):
        return len(self.auids)
    
    def _convert_key(self, key):
        """
        Used for getitem and setitem to determine whether the key is indexing
        the raw array (``raw``) or the active agents (``values``), and to convert
        the key to array indices if needed.
        """
        use_raw = True
        if isinstance(key, uids):
            pass
        elif isinstance(key, (BoolArr, IndexArr)):
            key = key.uids
        elif isinstance(key, (slice, int)):
            use_raw = False
        elif not np.isscalar(key) and len(key) == 0: # Handle [], np.array([]), etc.
            key = uids()
        else:
            errormsg = f'Indexing an Arr ({self.name}) by ({key}) is ambiguous or not supported. Use ss.uids() instead, or index Arr.raw or Arr.values.'
            raise Exception(errormsg)
        
        return key, use_raw
    
    def __getitem__(self, key):
        key, use_raw = self._convert_key(key)
        if use_raw:
            return self.raw[key]
        else:
            return self.values[key]
    
    def __setitem__(self, key, value):
        key, use_raw = self._convert_key(key)
        if use_raw:
            self.raw[key] = value
        else:
            self.raw[self.auids[key]] = value
            
    def __getattr__(self, attr):
        """ Make it behave like a regular array mostly -- enables things like sum(), mean(), etc. """
        if attr in ['__deepcopy__', '__getstate__', '__setstate__']:
            return self.__getattribute__(attr)
        else:
            return getattr(self.values, attr)
        
    def __gt__(self, other): return self.asnew(self.values > other,  cls=BoolArr)
    def __lt__(self, other): return self.asnew(self.values < other,  cls=BoolArr)
    def __ge__(self, other): return self.asnew(self.values >= other, cls=BoolArr)
    def __le__(self, other): return self.asnew(self.values <= other, cls=BoolArr)
    def __eq__(self, other): return self.asnew(self.values == other, cls=BoolArr)
    def __ne__(self, other): return self.asnew(self.values != other, cls=BoolArr)
    
    def __and__(self, other): raise BooleanOperationError(self)
    def __or__(self, other):  raise BooleanOperationError(self)
    def __xor__(self, other): raise BooleanOperationError(self)
    def __invert__(self):     raise BooleanOperationError(self)
    
    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        """ For almost everything else, behave like a normal NumPy array on Arr.values """
        inputs = [x.values if isinstance(x, Arr) else x for x in inputs]
        return getattr(ufunc, method)(*inputs, **kwargs)
    
    @property
    def auids(self):
        """ Link to the indices of active agents -- sim.people.auids """
        try:
            return self.people.auids
        except:
            print('Non-initialized States object')
            return uids(np.arange(len(self.raw)))
    
    def count(self):
        return np.count_nonzero(self.values)

    @property
    def values(self):
        """ Return the values of the active agents """
        return self.raw[self.auids]

    def set(self, uids, new_vals=None):
        """ Set the values for the specified UIDs"""
        if new_vals is None: 
            if isinstance(self.default, ss.Dist):
                new_vals = self.default.rvs(uids)
            elif callable(self.default):
                new_vals = self.default(len(uids))
            elif self.default is not None:
                new_vals = self.default
            else:
                new_vals = self.nan
        self.raw[uids] = new_vals
        return new_vals
    
    def set_nan(self, uids):
        """ Shortcut function to set values to NaN """
        self.raw[uids] = self.nan
        return
    
    def grow(self, new_uids=None, new_vals=None):
        """
        Add new agents to an Arr

        This method is normally only called via `People.grow()`.

        Args:
            new_uids: Numpy array of UIDs for the new agents being added
            new_vals: If provided, assign these state values to the new UIDs
        """
        if new_uids is None and new_vals is not None: # Used as a shortcut to avoid needing to supply twice
            new_uids = new_vals
        orig_len = self.len_used
        n_new = len(new_uids)
        self.len_used += n_new  # Increase the count of the number of agents by `n` (the requested number of new agents)
        
        # Physically reshape the arrays, if needed
        if orig_len + n_new > self.len_tot:
            n_grow = max(n_new, self.len_tot//2)  # Minimum 50% growth, since growing arrays is slow
            new_empty = np.empty(n_grow, dtype=self.dtype) # 10x faster than np.zeros()
            self.raw = np.concatenate([self.raw, new_empty], axis=0)
            self.len_tot = len(self.raw)
            if n_grow > n_new: # We added extra space at the end, set to NaN
                nan_uids = np.arange(self.len_used, self.len_tot)
                self.set_nan(nan_uids)
        
        # Set new values, and NaN if needed
        self.set(new_uids, new_vals=new_vals) # Assign new default values to those agents
        return
    
    def set_people(self, people):
        """ Reset the people object associated with this state """
        if isinstance(people, ss.People): # It's people, it's fine
            pass
        elif isinstance(people, ss.Sim): # Actually a sim
            people = people.people
        else:
            errormsg = f'Must supply a Sim or People object, not {type(people)}'
            raise TypeError(errormsg)
        assert people.initialized, 'People must be initialized before initializing states'
        self.people = people # Shorten since used a lot
        return

    def initialize(self, sim):
        """
        Initialize state

        This method should be called as part of initialization of the parent class containing the state -
        specifically, `People.initialize()` and `Module.initialize()`. Initialization of a State object
        involves two processes:

        - Converting any distribution objects to a Dist instance and linking it to RNGs stored in a `Sim`
        - Establishing a bidirectional connection with a `People` object for the purpose of UID indexing and resizing

        Since State objects can be stored in `People` or in a `Module` and the collection of all states in a `Sim` should
        be connected to RNGs within that same `Sim`, the states must necessarily be linked to the same `People` object that
        is inside a `Sim`. Initializing States outside of a `Sim` is not possible because of this RNG dependency, particularly
        because the states in a `People` object cannot be initialized without a `Sim` and therefore it would not be possible to
        have an initialized `People` object outside of a `Sim`.
        
        Args:
            sim: A `Sim` instance that contains an initialized `People` object
        """
        # Skip if already initialized
        if self.initialized:
            return

        # Establish connection with the People object
        people = sim.people
        self.set_people(people)
        people.register_state(self)
        
        # Connect any distributions in the default to RNGs in the Sim
        if isinstance(self.default, ss.Dist):
            self.default.initialize(module=self, sim=sim)

        # Populate initial values
        self.grow(people.uid)
        self.initialized = True
        return

    def asnew(self, arr=None, cls=None):
        """ Duplicate and copy (rather than link) data, optionally resetting the array """
        if cls is None:
            cls = self.__class__
        if arr is None:
            arr = self.values
        new = object.__new__(cls) # Create a new Arr instance
        new.__dict__ = self.__dict__.copy() # Copy pointers
        new.dtype = arr.dtype # Set to correct dtype
        new.raw = np.empty_like(new.raw, dtype=new.dtype) # Copy values, breaking reference
        new.raw[new.auids] = arr
        return new


class FloatArr(Arr):
    """ Subclass of Arr with defaults for floats """
    def __init__(self, name, default=None, nan=np.nan, label=None, skip_init=False):
        super().__init__(name=name, dtype=ss_float, default=default, nan=nan, label=label, coerce=False, skip_init=skip_init)
        return
    
    @property
    def isnan(self):
        """ Return indices that are NaN """
        return np.nonzero(np.isnan(self.values))[0]

    @property
    def notnan(self):
        """ Return indices that are not-NaN """
        return np.nonzero(~np.isnan(self.values))[0]
    
    @property
    def notnanvals(self):
        """ Return values that are not-NaN """
        vals = self.values # Shorten and avoid double indexing
        out = vals[np.nonzero(~np.isnan(vals))[0]]
        return out

    
class BoolArr(Arr):
    """ Subclass of Arr with defaults for booleans """
    def __init__(self, name, default=None, nan=False, label=None, skip_init=False): # No good NaN equivalent for bool arrays
        super().__init__(name=name, dtype=ss_bool, default=default, nan=nan, label=label, coerce=False, skip_init=skip_init)
        return
    
    def __and__(self, other): return self.asnew(self.values & other)
    def __or__(self, other):  return self.asnew(self.values | other)
    def __xor__(self, other): return self.asnew(self.values ^ other)
    def __invert__(self):     return self.asnew(~self.values)
    
    @property
    def uids(self):
        """ Convert True values to UIDs """
        return self.auids[np.nonzero(self.values)[0]]

    
class IndexArr(Arr):
    """ A special class of IndexArr used for UIDs and RNG IDs """
    def __init__(self, name, label=None):
        super().__init__(name=name, dtype=ss_int, default=None, nan=-1, label=label, coerce=False, skip_init=True)
        self.raw = uids(self.raw)
        return
    
    @property
    def uids(self):
        """ Alias to self.values, to allow Arr.uids like BoolArr """
        return self.values
    
    @property
    def isnan(self):
        return np.nonzero(self.values == self.nan)[0]

    @property
    def notnan(self):
        return np.nonzero(self.values != self.nan)[0]
    
    def grow(self, new_uids=None, new_vals=None):
        """ Change the size of the array """
        super().grow(new_uids=new_uids, new_vals=new_vals)
        self.raw = uids(self.raw)
        return
    
    
class uids(np.ndarray):
    """
    Class to specify that integers should be interpreted as UIDs.
    
    For all practical purposes, behaves like a NumPy integer array. However,
    has additional methods ``uids.concat()`` (instance method), ``ss.uids.cat()``
    (class method), ``uids.remove()``, and ``uids.intersect()`` to simplify common
    UID operations.    
    """
    def __new__(cls, arr=None):
        if arr is None:
            arr = np.empty(0, dtype=ss_int)
        return np.asarray(arr).view(cls)
    
    def concat(self, other, **kw): # Class and instance methods can't share a name
        """ Equivalent to np.concatenate(), but return correct type """
        return np.concatenate([self, other], **kw).view(self.__class__)
    
    @classmethod
    def cat(cls, *args, **kw):
        """ Equivalent to np.concatenate(), but return correct type """
        if len(args) == 0 or (len(args) == 1 and (args[0] is None or not len(args[0]))):
            return uids()
        arrs = args[0] if len(args) == 1 else args # TODO: handle one-array case
        return np.concatenate(arrs, **kw).view(cls)
    
    def remove(self, other, **kw):
        return np.setdiff1d(self, other, assume_unique=True, **kw).view(self.__class__)
    
    def intersect(self, other, **kw):
        return np.intersect1d(self, other, assume_unique=True, **kw).view(self.__class__)
    

class BooleanOperationError(NotImplementedError):
    """ Raised when a logical operation is performed on a non-logical array """
    def __init__(self, arr):
        msg = f'Logical operations are only valid on Boolean arrays, not {arr.dtype}'
        super().__init__(msg)