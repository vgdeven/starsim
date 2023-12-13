"""
Define default rsv disease module
"""

import numpy as np
import sciris as sc
import pandas as pd
from sciris import randround as rr

import stisim as ss
from .disease import Disease


__all__ = ['RSV']


class RSV(Disease):

    def __init__(self, pars=None, **kwargs):
        super().__init__(pars, **kwargs)

        self.rel_sus_imm = ss.State('rel_sus_imm', float, 1)
        self.rel_sus_vx = ss.State('rel_sus_vx', float, 1)
        self.rel_sev = ss.State('rel_sev', float, 1)
        self.rel_trans = ss.State('rel_trans', float, 1)

        # RSV states
        self.susceptible = ss.State('susceptible', bool, True)
        self.exposed = ss.State('exposed', bool, False)  # AKA incubating. Free of symptoms, not transmissible
        self.infected = ss.State('infected', bool, False)
        self.symptomatic = ss.State('symptomatic', bool, False)
        self.severe = ss.State('severe', bool, False)
        self.critical = ss.State('critical', bool, False)
        self.recovered = ss.State('recovered', bool, False)
        self.immune = ss.State('immune', bool, False)

        # Duration of stages
        self.dur_exposed = ss.State('dur_exposed', float, np.nan)
        self.dur_symptomatic = ss.State('dur_symptomatic', float, np.nan)
        self.dur_infection = ss.State('dur_infection', float, np.nan)  # Sum of all the stages
        self.dur_immune = ss.State('dur_immune', float, np.nan)

        # Timestep of state changes
        self.ti_exposed = ss.State('ti_exposed', int, ss.INT_NAN)
        self.ti_infected = ss.State('ti_infected', int, ss.INT_NAN)
        self.ti_symptomatic = ss.State('ti_symptomatic', int, ss.INT_NAN)
        self.ti_severe = ss.State('ti_severe', int, ss.INT_NAN)
        self.ti_critical = ss.State('ti_critical', int, ss.INT_NAN)
        self.ti_recovered = ss.State('ti_recovered', int, ss.INT_NAN)
        self.ti_susceptible = ss.State('ti_susceptible', int, ss.INT_NAN)
        self.ti_dead = ss.State('ti_dead', int, ss.INT_NAN)

        # Immunity state
        self.peak_nab = ss.State('nab', float, np.nan)

        # Parameters
        default_pars = dict(
            # RSV natural history
            dur_exposed=ss.lognormal(5, 2),  # https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4072624/
            dur_symptomatic=ss.lognormal(12, 20),  # https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4072624/
            dur_severe=ss.lognormal(20, 5),  # SOURCE
            dur_immune=ss.lognormal(100, 10),
            prognoses=dict(
                age_cutoffs=np.array([0, 1, 5, 15, 55]),  # Age cutoffs (lower limits)
                sus_ORs=np.array([2.50, 1.50, 0.25, 0.50, 1.00]),  # Odds ratios for relative susceptibility
                trans_ORs=np.array([1.00, 1.00, 1.00, 1.00, 1.00]),  # Odds ratios for relative transmissibility
                symp_probs=np.array([1, 0.84, 0.5, 0.2, 0.15]),  # Overall probability of developing symptoms
                severe_probs=np.array([0.050, 0.00050, 0.00050, 0.00050, 0.0050]), # Overall probability of developing severe symptoms
                crit_probs=np.array([0.0003, 0.00003, 0.00003, 0.00003, 0.00003]), # Overall probability of developing critical symptoms
                death_probs=np.array([0.00003, 0.00003, 0.00003, 0.00003, 0.00003]),  # Overall probability of dying
            ),
            beta_seasonality=1,
            phase_shift=5,

            # Initial conditions
            init_prev=0.03,
            # imm_init=1,
            # imm_decay=dict(form='growth_decay', growth_time=14, decay_rate=0.01),
            # immunity=None

        )
        self.pars = ss.omerge(default_pars, self.pars)

        return

    @property
    def infectious(self):
        return self.symptomatic

    @property
    def rel_sus(self):
        return self.rel_sus_imm * self.rel_sus_vx

    def initialize(self, sim):
        super().initialize(sim)
        # self.set_immunity(sim)
        return
    def init_results(self, sim):
        """ Initialize results """
        super().init_results(sim)
        return

    def get_woy(self, sim):
        year = sim.yearvec[sim.ti]
        days = int((year - int(year)) * 365.25)
        base_date = pd.to_datetime(f'{int(year)}-01-01')
        datetime = base_date + pd.DateOffset(days=days)
        return sc.date(datetime).isocalendar()[1]

    def get_seasonality(self, sim):
        woy = self.get_woy(sim)
        return (1 + self.pars['beta_seasonality'] * np.cos((2 * np.pi * woy / 52) + self.pars['phase_shift']))

    def set_initial_states(self, sim):
        """
        Set initial values for states. This could involve passing in a full set of initial conditions,
        or using init_prev, or other. Note that this is different to initialization of the State objects
        i.e., creating their dynamic array, linking them to a People instance. That should have already
        taken place by the time this method is called.
        """

        eligible_uids = ss.true(sim.people.age < 5)
        n_init_cases = int(self.pars['init_prev'] * len(eligible_uids))
        initial_cases = np.random.choice(eligible_uids, n_init_cases, replace=False)
        self.set_prognoses(sim, initial_cases)
        return

    def make_new_cases(self, sim):
        beta_seasonality = self.get_seasonality(sim)

        for k, layer in sim.people.networks.items():
            if k in self.pars['beta']:
                age_bins = np.digitize(sim.people.age, bins=self.pars['prognoses']['age_cutoffs']) - 1
                trans_OR = self.pars['prognoses']['trans_ORs'][age_bins]
                sus_OR = self.pars['prognoses']['sus_ORs'][age_bins]
                rel_trans = (self.infectious & sim.people.alive).astype(float) * trans_OR * self.rel_trans
                rel_sus = (self.susceptible & sim.people.alive).astype(float) * sus_OR * self.rel_sus
                for a, b, beta in [[layer.contacts['p1'], layer.contacts['p2'], self.pars['beta'][k]],
                                   [layer.contacts['p2'], layer.contacts['p1'], self.pars['beta'][k]]]:
                    # probability of a->b transmission
                    p_transmit = rel_trans[a] * rel_sus[b] * layer.contacts['beta'] * beta * beta_seasonality #* sim.dt
                    new_cases = np.random.random(len(a)) < p_transmit
                    if new_cases.any():
                        self.set_prognoses(sim, b[new_cases], source_uids=a[new_cases])
        return
    def update_results(self, sim):
        """ Update results """
        super().update_results(sim)
        return

    def update_pre(self, sim):
        """ Updates prior to interventions """

        self.check_symptomatic(sim)
        self.check_severe(sim)
        self.check_critical(sim)
        self.check_recovered(sim)
        self.check_susceptible(sim)

        return

    def check_symptomatic(self, sim):
        # Symptomatic
        symptomatic = self.exposed & (self.ti_symptomatic <= sim.ti)
        self.symptomatic[symptomatic] = True
        self.exposed[symptomatic] = False

        # Determine which symptomatic cases will become severe
        symptomatic_uids = ss.true(symptomatic)
        age_bins = np.digitize(sim.people.age[symptomatic],bins=self.pars['prognoses']['age_cutoffs']) - 1  # Age bins of individuals
        sev_probs = self.pars['prognoses']['severe_probs'][age_bins] * self.rel_sev[symptomatic_uids]
        dur_symptomatic = self.pars.dur_symptomatic(len(symptomatic_uids))/365 # duration in years
        severe_bools = ss.binomial_arr(sev_probs)
        severe_uids = symptomatic_uids[severe_bools]
        self.ti_severe[severe_uids] = sim.ti + rr(dur_symptomatic[severe_bools] / sim.dt)

        never_severe_uids = symptomatic_uids[~severe_bools]
        self.ti_recovered[never_severe_uids] = sim.ti + rr(dur_symptomatic[~severe_bools]/sim.dt)

    def check_severe(self, sim):
        # Severe
        severe = ~self.severe & ~self.critical & (self.ti_severe <= sim.ti)
        self.severe[severe] = True

        # Determine which severe cases will become critical
        age_bins = np.digitize(sim.people.age[severe], bins=self.pars['prognoses']['age_cutoffs']) - 1  # Age bins of individuals
        crit_probs = self.pars['prognoses']['crit_probs'][age_bins]
        severe_uids = ss.true(severe)
        dur_severe = self.pars.dur_symptomatic(len(severe_uids)) / 365  # duration in years
        crit_bools = ss.binomial_arr(crit_probs)
        crit_uids = severe_uids[crit_bools]
        never_crit_uids = severe_uids[~crit_bools]

        self.ti_critical[crit_uids] = sim.ti + rr(dur_severe[crit_bools] / sim.dt)
        self.ti_recovered[never_crit_uids] = sim.ti + rr(dur_severe[~crit_bools] / sim.dt)


    def check_critical(self, sim):
        # Critical
        critical = self.severe & (self.ti_critical <= sim.ti)
        self.critical[critical] = True


    def check_recovered(self, sim):
        # Recovered/immune
        recovered = self.infected & (self.ti_recovered <= sim.ti)
        self.recovered[recovered] = True
        self.immune[recovered] = True
        self.exposed[recovered] = False
        self.infected[recovered] = False
        self.symptomatic[recovered] = False
        self.severe[recovered] = False
        self.critical[recovered] = False

        self.ti_symptomatic[recovered] = ss.INT_NAN
        self.ti_severe[recovered] = ss.INT_NAN
        self.ti_critical[recovered] = ss.INT_NAN
        self.ti_recovered[recovered] = ss.INT_NAN

        recovered_uids = ss.true(recovered)

        dur_immune = self.pars.dur_immune(len(recovered_uids)) / 365
        self.dur_immune[recovered_uids] = dur_immune
        self.ti_susceptible[recovered_uids] = sim.ti + rr(dur_immune / sim.dt)


    def check_susceptible(self, sim):
        # Susceptible
        susceptible = self.immune & (self.ti_susceptible <= sim.ti)
        self.immune[susceptible] = False
        self.susceptible[susceptible] = True

    def record_exposure(self, sim, uids):
        self.susceptible[uids] = False
        self.exposed[uids] = True
        self.infected[uids] = True
        self.ti_exposed[uids] = sim.ti
        self.ti_infected[uids] = sim.ti

    def set_prognoses(self, sim, target_uids, source_uids=None):
        """
        Natural history of RSV
        """

        # Subset target_uids to only include ones who are infectious
        if source_uids is not None:
            infectious_sources = self.symptomatic[source_uids].values.nonzero()[-1]
            uids = target_uids[infectious_sources]
        else:
            uids = target_uids

        n_uids = len(uids)
        self.record_exposure(sim, uids)

        # Determine which infections will become symptomatic
        age_bins = np.digitize(sim.people.age[uids], bins=self.pars['prognoses']['age_cutoffs']) - 1  # Age bins of individuals
        symp_probs = self.pars['prognoses']['symp_probs'][age_bins] * self.rel_sev[uids]
        symptomatic_uids = uids[ss.binomial_arr(symp_probs)]
        never_symptomatic_uids = np.setdiff1d(uids, symptomatic_uids)

        # Exposed to symptomatic
        dur_exposed = self.pars.dur_exposed(n_uids)/365 # duration in years
        self.dur_exposed[uids] = dur_exposed
        self.ti_symptomatic[symptomatic_uids] = sim.ti + rr(self.dur_exposed[symptomatic_uids].values/sim.dt)

        # Never symptomatic, set day recover
        self.ti_recovered[never_symptomatic_uids] = sim.ti + rr(self.dur_exposed[never_symptomatic_uids].values / sim.dt)

        return

    def set_immunity(self, sim):
        self.pars['imm_kin'] = self.precompute_waning(length=sim.npts, pars=self.pars['imm_decay'])
        return

    def precompute_waning(self, length, pars):
        '''
        Process functional form and parameters into values:

            - 'growth_decay' : linear growth followed by exponential decay
            - 'exp_decay'   : exponential decay. Parameters should be init_val and half_life (half_life can be None/nan)
            - 'linear_decay': linear decay

        A custom function can also be supplied.

        Args:
            length (float): length of array to return, i.e., for how long waning is calculated
            pars (dict): passed to individual immunity functions

        Returns:
            array of length 'length' of values
        '''

        pars = sc.dcp(pars)
        form = pars.pop('form')
        choices = [
            'growth_decay',  # Default if no form is provided
            # 'exp_decay',
        ]

        # Process inputs
        if form is None or form == 'growth_decay':
            output = self.growth_decay(length, **pars)

        elif callable(form):
            output = form(length, **pars)

        else:
            errormsg = f'The selected functional form "{form}" is not implemented; choices are: {sc.strjoin(choices)}'
            raise NotImplementedError(errormsg)

        return output

    def growth_decay(self, length, growth_time, decay_rate):
        '''
        Returns an array of length 'length' containing the evaluated growth/decay
        function at each point.

        Uses linear growth + exponential decay.

        Args:
            length (int): number of points
            growth_time (int): length of time immunity grows (used to determine slope)
            decay_rate (float): rate of exponential decay
        '''

        def f1(t, growth_time):
            '''Simple linear growth'''
            return (1 / growth_time) * t

        def f2(t, decay_rate):
            decayRate = np.full(len(t), fill_value=decay_rate)
            titre = np.zeros(len(t))
            for i in range(1, len(t)):
                titre[i] = titre[i - 1] + decayRate[i]
            return np.exp(-titre)

        length = length + 1
        t1 = np.arange(growth_time, dtype=int)
        t2 = np.arange(length - growth_time, dtype=int)
        y1 = f1(t1, growth_time)
        y2 = f2(t2, decay_rate)
        y = np.concatenate([y1, y2])
        y = y[0:length]

        return y

