from time import sleep

from perfrunner.helpers.cbmonitor import with_stats
from perfrunner.helpers.misc import log_phase, target_hash
from perfrunner.settings import TargetSettings
from perfrunner.tests import PerfTest, TargetIterator


class XdcrTest(PerfTest):

    """
    Base XDCR test and also full implementation of bi-directional case.

    As a base class it implements several methods for XDCR management and WAN
    configuration.

    "run" workflow is common for both uni-directional and bi-directional cases.
    """

    COLLECTORS = {'latency': True, 'xdcr_lag': True, 'xdcr_stats': True}

    ALL_BUCKETS = True

    def __init__(self, *args, **kwargs):
        super(XdcrTest, self).__init__(*args, **kwargs)
        self.settings = self.test_config.xdcr_settings

    def _start_replication(self, m1, m2):
        name = target_hash(m1, m2)
        certificate = self.settings.use_ssl and self.rest.get_certificate(m2)
        self.rest.add_remote_cluster(m1, m2, name, certificate)

        for bucket in self.test_config.buckets:
            params = {
                'replicationType': 'continuous',
                'toBucket': bucket,
                'fromBucket': bucket,
                'toCluster': name
            }
            if self.settings.replication_protocol:
                params['type'] = self.settings.replication_protocol
            if self.settings.filter_expression:
                params['filterExpression'] = self.settings.filter_expression
            self.rest.start_replication(m1, params)

    def enable_xdcr(self):
        m1, m2 = self.cluster_spec.yield_masters()

        if self.settings.replication_type == 'unidir':
            self._start_replication(m1, m2)
        if self.settings.replication_type == 'bidir':
            self._start_replication(m1, m2)
            self._start_replication(m2, m1)

    def monitor_replication(self):
        sleep(self.MONITORING_DELAY)
        for target in self.target_iterator:
            self.monitor.monitor_xdcr_queues(target.node, target.bucket)

    @with_stats
    def access(self):
        super(XdcrTest, self).timer()

    def configure_wan(self):
        if self.settings.wan_enabled:
            hostnames = tuple(self.cluster_spec.yield_hostnames())
            src_list = [
                hostname for hostname in hostnames[len(hostnames) / 2:]
            ]
            dest_list = [
                hostname for hostname in hostnames[:len(hostnames) / 2]
            ]
            self.remote.enable_wan()
            self.remote.filter_wan(src_list, dest_list)

    def _report_kpi(self):
        self.reporter.post_to_sf(*self.metric_helper.calc_xdcr_lag())

        if self.test_config.stats_settings.post_cpu:
            self.reporter.post_to_sf(
                *self.metric_helper.calc_cpu_utilization()
            )

    def run(self):
        if self.test_config.restore_settings.snapshot and self.build > '4':
            self.restore()
        else:
            self.load()
            self.wait_for_persistence()

        self.enable_xdcr()
        self.monitor_replication()
        self.wait_for_persistence()

        self.hot_load()

        self.configure_wan()

        self.workload = self.test_config.access_settings
        self.access_bg()
        self.access()

        self.report_kpi()


class SrcTargetIterator(TargetIterator):

    def __iter__(self):
        password = self.test_config.bucket.password
        prefix = self.prefix
        src_master = self.cluster_spec.yield_masters().next()
        for bucket in self.test_config.buckets:
            if self.prefix is None:
                prefix = target_hash(src_master, bucket)
            yield TargetSettings(src_master, bucket, password, prefix)


class DestTargetIterator(TargetIterator):

    def __iter__(self):
        password = self.test_config.bucket.password
        prefix = self.prefix
        masters = self.cluster_spec.yield_masters()
        src_master = masters.next()
        dest_master = masters.next()
        for bucket in self.test_config.buckets:
            if self.prefix is None:
                prefix = target_hash(src_master, bucket)
            yield TargetSettings(dest_master, bucket, password, prefix)


class UniDirXdcrTest(XdcrTest):

    """
    Uni-directional XDCR benchmarks.
    """

    def __init__(self, *args, **kwargs):
        super(UniDirXdcrTest, self).__init__(*args, **kwargs)
        self.target_iterator = TargetIterator(self.cluster_spec,
                                              self.test_config,
                                              prefix='symmetric')

    def load(self):
        load_settings = self.test_config.load_settings
        log_phase('load phase', load_settings)
        src_target_iterator = SrcTargetIterator(self.cluster_spec,
                                                self.test_config,
                                                prefix='symmetric')
        self.worker_manager.run_workload(load_settings, src_target_iterator)
        self.worker_manager.wait_for_workers()


class XdcrInitTest(UniDirXdcrTest):

    """
    The test covers scenario when 2 cluster are synced up for the first time.
    There is no ongoing workload and compaction is usually disabled.
    """

    COLLECTORS = {'xdcr_stats': True}

    def load(self):
        load_settings = self.test_config.load_settings
        log_phase('load phase', load_settings)
        src_target_iterator = SrcTargetIterator(self.cluster_spec,
                                                self.test_config)
        self.worker_manager.run_workload(load_settings, src_target_iterator)
        self.worker_manager.wait_for_workers()

    @with_stats
    def init_xdcr(self):
        self.enable_xdcr()
        self.monitor_replication()

    def run(self):
        if self.test_config.restore_settings.snapshot and self.build > '4':
            self.restore()
        else:
            self.load()
            self.wait_for_persistence()

        self.configure_wan()

        from_ts, to_ts = self.init_xdcr()
        self.time_elapsed = (to_ts - from_ts) / 1000.0
        self.reporter.finish('Initial replication', self.time_elapsed)
        self.report_kpi()

    def _report_kpi(self):
        rate = self.metric_helper.calc_avg_replication_rate(self.time_elapsed)
        self.reporter.post_to_sf(rate)
