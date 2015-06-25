from scipy.optimize import minimize
import numpy


class ShiftedBeta(object):
    """
    This class implements the Shifted-Beta model by P. Fader and B. Hardie,
    however, unlike the original paper, we take the bayesian route and compute
    directly the distributions of parameters alpha and beta using MCMC. These,
    in turn, are used to estimate the expected values of tenure and LTV.

    This model works by assuming a constant in time, beta distributed
    individual probability of churn. Due to the heterogeneity of a cohort's
    churn rates (since each individual will have a different probability of
    churning), expected behaviours such as the decrease of cohort churn rate
    over time arise naturally.

    To train the model we need time evolution of a cohort's population in the
    form:
        c1 = [N_0, N_1, ...]

    Since we have multiple cohorts coexisting at any given month we may
    leverage all this information to train the model.
        c1 = [N1_0, N1_1, ...]
        c2 = [N2_0, N2_1, ...]
        ...
        data = [c1, c2, ...]
    """

    def __init__(self, data):
        """

        :param data:
        :return:
        """

        self.data = data

        self.categories = sorted(data.keys())
        self.n_cats = len(data)

        # params constructor
        self.imap = {}
        self.indicator_map()

        # ps
        self.alpha = {}
        self.beta = {}

        # ops obj
        self.opt = None

    def indicator_map(self):
        """
        indicator_map constructs a boolean vector indicating which parameters
        to use for a given predictor.

        alpha and beta paramaters are assumed to be linear combination like:

            alpha = alpha0 + alpha1 * predictor1 + alpha2 * predictor2 + ...

        and similarly for beta. However, as it stands, predictors are
        one-hot encoded categorical variables, so at any given time at most
        only two alpha_i are used, the intercept and coefficient of the
        current predictor. The indicator_map methods takes care of keeping
        track of that.
        """

        # For each category in the data turn on a different combination of a
        # boolean array.
        for i, category in enumerate(self.categories):

            # Initial a boolean array as false, with length equal to the
            # number of available categories.
            bool_ind = numpy.zeros(self.n_cats, dtype=bool)

            # The intercept (index = 0) is always on.
            bool_ind[0] = True

            # For any category but the first, both the intercept as well as an
            # extra entry are set to True.
            bool_ind[i] = True

            # Change the instance variable imap in place by adding the
            # appropriate key: bool array pair.
            self.imap[category] = bool_ind

    @staticmethod
    def _recursive_retention_stats(alpha, beta, num_periods):
        """
        A function to calculate the expected probabilities recursively.
        Using equation 7 from [1] and the alpha and beta coefficients
        obtained by training this model, it computes P(T = t) recursively,
        returning a list with all values.

        Survival function recursive calculation. Using equation 7 from [1]
        and the alpha and beta coefficients obtained by training this
        model, it computes S(T = t) recursively, returning a list of all
        computed values.. To do so it must first invoke the function
        P_T_is_t calculate the monthly churn rates for the given time
        window, and then use it to compute the survival curve recursively.

        :param alpha: float
            The distribution for the alpha parameter.

        :param beta: float
            The distribution for the beta parameter.

        :param num_periods: Int
            The number of periods for which the probability of churning
            should be computed.

        :return: (list, list)
            A list with probability of churning for all periods from month
            zero to num_periods.
        """

        # Initialize list with t = 0 and t = 1 values
        p = [None, alpha / (alpha + beta)]
        s = [None, 1 - p[1]]

        for t in range(2, num_periods):
            # Compute latest p value and appen
            pt = (beta + t - 2) / (alpha + beta + t - 1) * p[t-1]
            p.append(pt)

            # use the most recent appended p value to keep building s
            s.append(s[t-1] - p[t])

        # finish this...
        return p, s

    def _logp(self, alpha, beta):
        """
        The LogLikelihood function. Given the data and relevant
        variables this function computed the loglikelihood.

        :param alpha: float
            aaa

        :param alpha: float
            bbb

        :return: Float
            Minus the LogLikelihood of the model.
        """

        # --- LogLikelihood (One Cohort at a Time) --- #
        # We calculate the LogLikelihood for each cohort separately and
        # combining them. From appendix B in [1] it is easy to see that
        # the extension of the model to multiple cohorts of different
        # sizes is simply given a similar product as in B1, except that
        # each month of each cohort will contribute with a term like:
        #       P(T = t | alpha, beta) ** n_t
        # Which, when taking the log, translates to a sum similar to B3,
        # but extended to include all cohorts.
        log_like = 0.0

        for name, data in self.data.iteritems():

            bool_ind = self.imap[name]

            alpha_comb = numpy.exp(alpha[bool_ind].sum())
            beta_comb = numpy.exp(beta[bool_ind].sum())

            # A loop through each element in the data list. Remember that
            # each element correspond to a particular cohort data. The loop
            # simply carries out the calculation in B3, appendix B, [1].
            for i, val in enumerate(data):

                # The number of customer that are still active and the
                # number of customers lost at each month for which cohort
                # data is available.
                active, lost = val

                # Since the original dataset was augmented earlier in this
                # method, we must specify the point at which the
                # calculations performed here should stop. In other words,
                # length indicates the point at which actual data is
                # available.
                length = len(active)

                # stuff...#
                pt, sf = self._recursive_retention_stats(alpha=alpha_comb,
                                                         beta=beta_comb,
                                                         num_periods=length)

                # Likelihood of observing such data given the model.
                # Refer to equation B3 for context.
                # *** Note that the data is used only up to index length,
                # hence avoiding the inclusion of augmented data points.
                # ***
                died = numpy.log(pt[1:length]) * lost[1:length]

                # Likelihood of having this many people left after
                # some time
                still_active = numpy.log(sf[length - 1]) * active[length - 1]

                # Update the log_like value.
                log_like += sum(died) + still_active

        # Negative log_like since we will use scipy's minimize object.
        return -log_like

    def fit(self, restarts=50):
        """

        :param restarts:
        :return:
        """

        # guesses of initial parameters
        initial_guesses = 4 * numpy.random.random((restarts, 2 * self.n_cats)) - 3

        # Initialize optimal value to None
        # I choose not to set it a, say, zero, or any other number, since I am
        # not sure that the log-likelihood is bounded in anyway. So is better to
        # initialize with None and use the first optimal value start the ball
        # rolling.
        optimal = None

        # Run likelihood optimization for several steps...
        # noinspection PyTypeChecker
        for guess in initial_guesses:

            # --- Optimization
            # something...
            new_opt = minimize(lambda p: self._logp(p[:self.n_cats],
                                                    p[self.n_cats:]),
                               guess,
                               bounds=[(None, None)] * 2 * self.n_cats
                               )

            # If first run...
            if optimal is None:
                optimal = new_opt.fun
                self.opt = new_opt.x

            # Have we found a better value yet?
            if new_opt > optimal:
                optimal = new_opt.fun
                self.opt = new_opt.x

        # Values for all categories.
        for name in self.categories:

            # Is boolean ideal?
            bool_ind = self.imap[name]

            self.alpha[name] = numpy.exp(self.opt[:self.n_cats][bool_ind].sum())
            self.beta[name] = numpy.exp(self.opt[self.n_cats:][bool_ind].sum())

    def get_coeffs(self):
        """

        :return:
        """

        coeffs = {}

        for (c1, a), (c2, b) in zip(self.alpha.items(),
                                    self.beta.items()):
            coeffs[c1] = dict(alpha=a,
                              beta=b)

        return coeffs

    def get_params(self):

        params = dict(n_categories=self.n_cats,
                      categories=self.categories,
                      imap=self.imap,
                      )

        return params