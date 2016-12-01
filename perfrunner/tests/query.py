from perfrunner.helpers.cbmonitor import with_stats
from perfrunner.tests.index import DevIndexTest, IndexTest


class QueryTest(IndexTest):

    """
    The base test which defines workflow for different view query tests. Access
    phase represents mixed KV workload and queries on views.
    """

    COLLECTORS = {'latency': True, 'query_latency': True}

    @with_stats
    def access(self, *args):
        super(QueryTest, self).timer()

    def run(self):
        self.load()
        self.wait_for_persistence()

        self.compact_bucket()

        self.hot_load()

        self.define_ddocs()
        self.build_index()

        self.workload = self.test_config.access_settings
        self.access_bg()
        self.access()

        self.report_kpi()


class QueryThroughputTest(QueryTest):

    """
    The test adds a simple step to workflow: post-test calculation of average
    query throughput.
    """

    def _report_kpi(self):
        self.reporter.post_to_sf(
            self.metric_helper.calc_avg_couch_views_ops()
        )


class QueryLatencyTest(QueryTest):

    """The basic test for bulk latency measurements. Bulk means a mix of
    equality, range, group, and etc. The most reasonable test for update_after
    (stale) queries.

    The class itself only adds calculation and posting of query latency.
    """

    def _report_kpi(self):
        self.reporter.post_to_sf(
            *self.metric_helper.calc_query_latency(percentile=80)
        )


class IndexLatencyTest(QueryTest):

    """
    Measurement of end-to-end latency which is defined as time it takes for a
    document to appear in view output after it is stored in KV.

    The test only adds calculation phase. See cbagent project for details.
    """

    COLLECTORS = {'index_latency': True, 'query_latency': True}

    def _report_kpi(self):
        self.reporter.post_to_sf(
            *self.metric_helper.calc_observe_latency(percentile=95)
        )


class DevQueryLatencyTest(DevIndexTest, QueryLatencyTest):

    """
    Per query type latency measurements.
    """

    pass
