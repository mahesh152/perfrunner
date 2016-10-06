from collections import OrderedDict

import numpy as np
from logger import logger
from seriesly import Seriesly

from perfrunner.helpers.cbmonitor import CbAgent
from perfrunner.helpers.remote import RemoteHelper


class MetricHelper(object):

    def __init__(self, test):
        self.test = test
        self.seriesly = Seriesly(
            test.test_config.stats_settings.seriesly['host'])
        self.test_config = test.test_config
        self.metric_title = test.test_config.test_case.metric_title
        self.cluster_spec = test.cluster_spec
        self.cluster_names = test.cbagent.clusters.keys()
        self.build = test.build
        self.master_node = test.master_node
        self.in_bytes_transfer = []
        self.out_bytes_transfer = []

    @staticmethod
    def _get_query_params(metric, from_ts=None, to_ts=None):
        """Convert metric definition to Seriesly query params. E.g.:

        'avg_xdc_ops' -> {'ptr': '/xdc_ops',
                          'group': 1000000000000, 'reducer': 'avg'}

        Where group is constant."""
        params = {'ptr': '/{}'.format(metric[4:]),
                  'reducer': metric[:3],
                  'group': 1000000000000}
        if from_ts and to_ts:
            params.update({'from': from_ts, 'to': to_ts})
        return params

    def _get_metric_info(self, title, larger_is_better=False, level='Basic',
                         orderbymetric=None):
        return {'title': title,
                'cluster': self.cluster_spec.name,
                'larger_is_better': str(larger_is_better).lower(),
                'level': level,
                'orderby': orderbymetric}

    def calc_ycsb_queries(self, value, name, title, larger_is_better=True):
        metric = '{}_{}_{}'.format(self.test_config.name, name,
                                   self.cluster_spec.name)
        title = '{} , {}'.format(title, self.metric_title)
        metric_info = self._get_metric_info(title, larger_is_better)
        return value, metric, metric_info

    def calc_avg_xdcr_ops(self):
        metric = '{}_avg_xdcr_ops_{}'.format(self.test_config.name,
                                             self.cluster_spec.name)
        title = 'Avg. XDCR ops/sec, {}'.format(self.metric_title)
        metric_info = self._get_metric_info(title, larger_is_better=True)
        query_params = self._get_query_params('avg_xdc_ops')

        xdcr_ops = 0
        for bucket in self.test_config.buckets:
            db = 'ns_server{}{}'.format(self.cluster_names[1], bucket)
            data = self.seriesly[db].query(query_params)
            xdcr_ops += data.values()[0][0]
        xdcr_ops = round(xdcr_ops, 1)

        return xdcr_ops, metric, metric_info

    def calc_avg_set_meta_ops(self):
        metric = '{}_avg_set_meta_ops_{}'.format(self.test_config.name,
                                                 self.cluster_spec.name)
        title = 'Avg. XDCR rate (items/sec), {}'.format(self.metric_title)
        metric_info = self._get_metric_info(title, larger_is_better=True)
        query_params = self._get_query_params('avg_ep_num_ops_set_meta')

        set_meta_ops = 0
        for bucket in self.test_config.buckets:
            db = 'ns_server{}{}'.format(self.cluster_names[1], bucket)
            data = self.seriesly[db].query(query_params)
            set_meta_ops += data.values()[0][0]
        set_meta_ops = round(set_meta_ops, 1)

        return set_meta_ops, metric, metric_info

    def calc_avg_n1ql_queries(self):
        metric = '{}_avg_query_requests_{}'.format(self.test_config.name,
                                                   self.cluster_spec.name)
        title = 'Avg. Query Throughput (queries/sec), {}'.format(self.metric_title)
        metric_info = self._get_metric_info(title, larger_is_better=True)

        queries = self._calc_avg_n1ql_queries()
        return queries, metric, metric_info

    def _calc_avg_n1ql_queries(self):
        query_params = self._get_query_params('avg_query_requests')
        db = 'n1ql_stats{}'.format(self.cluster_names[0])
        data = self.seriesly[db].query(query_params)
        queries = data.values()[0][0]

        return round(queries, 1)

    def cal_avg_n1ql_queries_perfdaily(self):
        return {"name": "avg_query_throughput",
                "description": "Avg. Query Throughput (queries/sec)",
                "value": self._calc_avg_n1ql_queries(),
                "larger_is_better": self.test.test_config.test_case.larger_is_better.lower() == "true",
                "threshold": self.test.test_config.dailyp_settings.threshold
                }

    def parse_log(self, test_config, name):
        cbagent = CbAgent(self.test, verbose=False)
        if name.find('Elasticsearch') != -1:
            cbagent.prepare_elastic_stats(cbagent.clusters.keys(), test_config)
        else:
            cbagent.prepare_fts_query_stats(cbagent.clusters.keys(), test_config)
        '''
         we currently have the logs.
         From the logs we will get the latest result
        '''
        fts_es = cbagent.fts_stats
        fts_es.collect_stats()
        total = fts_es.cbft_query_total()
        return total

    def calc_avg_fts_queries(self, orderbymetric, name='FTS'):
        metric = '{}_avg_query_requests_{}'.format(self.test_config.name,
                                                   self.cluster_spec.name)
        hosts = [x for x in self.cluster_spec.yield_servers()]
        title = 'Query Throughput (queries/sec), {}, {} node, {}'.format(self.metric_title,
                                                                         len(hosts), name)
        metric_info = self._get_metric_info(title, larger_is_better=True,
                                            orderbymetric=orderbymetric)
        total_queries = self.parse_log(self.test_config, name)
        time_taken = self.test_config.access_settings.time
        qps = total_queries / time_taken
        return round(qps, 1), metric, metric_info

    def calc_latency_ftses_queries(self, percentile, dbname,
                                   metrics, orderbymetric, name='FTS'):
        metric = '{}_{}'.format(self.test_config.name, self.cluster_spec.name)
        hosts = [x for x in self.cluster_spec.yield_servers()]
        title = '{}th percentile query latency (ms), {}, {} node, {}'.\
            format(percentile, self.metric_title, len(hosts), name)
        metric_info = self._get_metric_info(title, larger_is_better=False,
                                            orderbymetric=orderbymetric)
        timings = []
        db = '{}{}'.format(dbname, self.cluster_names[0])
        data = self.seriesly[db].get_all()
        timings += [v[metrics] for v in data.values()]
        fts_latency = round(np.percentile(timings, percentile), 2)
        return round(fts_latency), metric, metric_info

    def calc_ftses_index(self, elapsedtime, orderbymetric, name='FTS'):
        metric = '{}_{}'.format(self.test_config.name, self.cluster_spec.name)
        hosts = [x for x in self.cluster_spec.yield_servers()]
        title = 'Index Throughput(sec), {}, {} node, {}'.format(self.metric_title, len(hosts), name)
        metric_info = self._get_metric_info(title, larger_is_better=False,
                                            orderbymetric=orderbymetric)
        return round(elapsedtime, 1), metric, metric_info

    def calc_avg_ops(self):
        """Returns the average operations per second."""
        metric = '{}_avg_ops_{}'.format(self.test_config.name,
                                        self.cluster_spec.name)
        title = 'Average ops/sec, {}'.format(self.metric_title)
        metric_info = self._get_metric_info(title, larger_is_better=True)

        ops = self._calc_avg_ops()
        return ops, metric, metric_info

    def _calc_avg_ops(self):
        query_params = self._get_query_params('avg_ops')
        ops = 0
        for bucket in self.test_config.buckets:
            db = 'ns_server{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].query(query_params)
            ops += data.values()[0][0]
        return int(ops)

    def calc_avg_ops_perfdaily(self):
        return {"name": 'average_throughput',
                "description": 'Average Throughput (ops/sec)',
                "value": self._calc_avg_ops(),
                "larger_is_better": self.test.test_config.test_case.larger_is_better.lower() == "true",
                "threshold": self.test.test_config.dailyp_settings.threshold
                }

    def calc_xdcr_lag(self, percentile=90):
        metric = '{}_{}th_xdc_lag_{}'.format(self.test_config.name,
                                             percentile,
                                             self.cluster_spec.name)
        title = '{}th percentile replication lag (ms), {}'.format(
            percentile, self.metric_title)
        metric_info = self._get_metric_info(title)
        query_params = self._get_query_params('avg_xdcr_lag')
        query_params.update({'group': 1000})

        timings = []
        for bucket in self.test_config.buckets:
            db = 'xdcr_lag{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].query(query_params)
            timings = [v[0] for v in data.values()]
        lag = round(np.percentile(timings, percentile))

        return lag, metric, metric_info

    def calc_replication_changes_left(self):
        metric = '{}_avg_replication_queue_{}'.format(self.test_config.name,
                                                      self.cluster_spec.name)
        title = 'Avg. replication queue, {}'.format(self.metric_title)
        metric_info = self._get_metric_info(title)
        query_params = self._get_query_params('avg_replication_changes_left')

        queues = 0
        for bucket in self.test_config.buckets:
            db = 'ns_server{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].query(query_params)
            queues += data.values()[0][0]
        queue = round(queues)

        return queue, metric, metric_info

    def calc_avg_replication_rate(self, time_elapsed):
        initial_items = self.test_config.load_settings.ops or \
            self.test_config.load_settings.items
        num_buckets = self.test_config.cluster.num_buckets
        avg_replication_rate = num_buckets * initial_items / time_elapsed

        return round(avg_replication_rate)

    def calc_max_drain_rate(self, time_elapsed):
        items_per_node = self.test_config.load_settings.items / \
            self.test_config.cluster.initial_nodes[0]
        drain_rate = items_per_node / time_elapsed

        return round(drain_rate)

    def calc_avg_disk_write_queue(self):
        query_params = self._get_query_params('avg_disk_write_queue')

        disk_write_queue = 0
        for bucket in self.test_config.buckets:
            db = 'ns_server{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].query(query_params)
            disk_write_queue += data.values()[0][0]

        return round(disk_write_queue / 10 ** 6, 2)

    def calc_avg_ep_bg_fetched(self):
        query_params = self._get_query_params('avg_ep_bg_fetched')

        ep_bg_fetched = 0
        for bucket in self.test_config.buckets:
            db = 'ns_server{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].query(query_params)
            ep_bg_fetched += data.values()[0][0]
        ep_bg_fetched /= self.test_config.cluster.initial_nodes[0]

        return round(ep_bg_fetched)

    def calc_avg_bg_wait_time(self):
        query_params = self._get_query_params('avg_avg_bg_wait_time')

        avg_bg_wait_time = []
        for bucket in self.test_config.buckets:
            db = 'ns_server{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].query(query_params)
            avg_bg_wait_time.append(data.values()[0][0])
        avg_bg_wait_time = np.mean(avg_bg_wait_time) / 10 ** 3  # us -> ms

        return round(avg_bg_wait_time, 2)

    def calc_avg_couch_views_ops(self):
        query_params = self._get_query_params('avg_couch_views_ops')

        couch_views_ops = 0
        for bucket in self.test_config.buckets:
            db = 'ns_server{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].query(query_params)
            couch_views_ops += data.values()[0][0]

        if self.build < '2.5.0':
            couch_views_ops /= self.test_config.cluster.initial_nodes[0]

        return round(couch_views_ops)

    def calc_query_latency(self, percentile):
        metric = '{}_{}'.format(self.test_config.name, self.cluster_spec.name)
        if 'MG7' in metric:
            title = '{}th percentile query latency, {}'.format(percentile,
                                                               self.metric_title)
        else:
            title = '{}th percentile query latency (ms), {}'.format(percentile,
                                                                    self.metric_title)
        metric_info = self._get_metric_info(title)
        query_latency = self._calc_query_latency(percentile)

        return query_latency, metric, metric_info

    def _calc_query_latency(self, percentile):
        timings = []
        for bucket in self.test_config.buckets:
            db = 'spring_query_latency{}{}'.format(self.cluster_names[0],
                                                   bucket)
            data = self.seriesly[db].get_all()
            timings += [value['latency_query'] for value in data.values()]
        query_latency = np.percentile(timings, percentile)
        return round(query_latency, 2)

    def calc_query_latency_perfdaily(self, percentile):
        return {"name": '{}th_percentile_query_latency'.format(percentile),
                "description": '{}th percentile Query Latency (ms)'.format(percentile),
                "value": self._calc_query_latency(percentile),
                "larger_is_better": self.test.test_config.test_case.larger_is_better.lower() == "true",
                "threshold": self.test.test_config.dailyp_settings.threshold
                }

    def calc_secondaryscan_latency(self, percentile):
        metric = '{}_{}'.format(self.test_config.name, self.cluster_spec.name)
        title = '{}th percentile secondary scan latency (ms), {}'.format(percentile,
                                                                         self.metric_title)
        metric_info = self._get_metric_info(title)

        timings = []
        db = 'secondaryscan_latency{}'.format(self.cluster_names[0])
        data = self.seriesly[db].get_all()
        timings += [value[' Nth-latency'] for value in data.values()]
        timings = map(int, timings)
        logger.info("Number of samples are {}".format(len(timings)))
        logger.info("Timings: {}".format(timings))
        secondaryscan_latency = np.percentile(timings, percentile) / 1000000

        return round(secondaryscan_latency, 2), metric, metric_info

    def calc_kv_latency(self, operation, percentile, dbname='spring_latency'):
        """Calculate kv latency
        :param operation:
        :param percentile:
        :param dbname:  Same procedure is used for KV and subdoc .
            for KV dbname will spring_latency.For subdoc will be spring_subdoc_latency
        :return:
        """
        metric = '{}_{}_{}th_{}'.format(self.test_config.name,
                                        operation,
                                        percentile,
                                        self.cluster_spec.name
                                        )
        title = '{}th percentile {} {}'.format(percentile,
                                               operation.upper(),
                                               self.metric_title)
        metric_info = self._get_metric_info(title)
        latency = self._calc_kv_latency(operation, percentile, dbname)

        return latency, metric, metric_info

    def _calc_kv_latency(self, operation, percentile, dbname):
        timings = []
        op_key = 'latency_{}'.format(operation)
        for bucket in self.test_config.buckets:
            db = '{}{}{}'.format(dbname, self.cluster_names[0], bucket)
            data = self.seriesly[db].get_all()
            timings += [
                v[op_key] for v in data.values() if op_key in v
            ]
        return round(np.percentile(timings, percentile), 2)

    def calc_kv_latency_perfdaily(self, operation, percentile, dbname='spring_latency'):
        return {"name": '{}th_percentile_{}_latency'.format(percentile, operation),
                "description": '{}th percentile {} Latency (ms)'.format(percentile, operation.upper()),
                "value": self._calc_kv_latency(operation, percentile, dbname),
                "larger_is_better": self.test.test_config.test_case.larger_is_better.lower() == "true",
                "threshold": self.test.test_config.dailyp_settings.threshold
                }

    def calc_observe_latency(self, percentile):
        metric = '{}_{}th_{}'.format(self.test_config.name, percentile,
                                     self.cluster_spec.name)
        title = '{}th percentile {}'.format(percentile, self.metric_title)
        metric_info = self._get_metric_info(title)

        timings = []
        for bucket in self.test_config.buckets:
            db = 'observe{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].get_all()
            timings += [v['latency_observe'] for v in data.values()]
        latency = round(np.percentile(timings, percentile), 2)

        return latency, metric, metric_info

    def calc_cpu_utilization(self):
        metric = '{}_avg_cpu_{}'.format(self.test_config.name,
                                        self.cluster_spec.name)
        title = 'Avg. CPU utilization (%)'
        title = '{}, {}'.format(title, self.metric_title)
        metric_info = self._get_metric_info(title)

        cluster = self.cluster_names[0]
        bucket = self.test_config.buckets[0]

        query_params = self._get_query_params('avg_cpu_utilization_rate')
        db = 'ns_server{}{}'.format(cluster, bucket)
        data = self.seriesly[db].query(query_params)
        cpu_utilazion = round(data.values()[0][0])

        return cpu_utilazion, metric, metric_info

    def calc_views_disk_size(self, from_ts=None, to_ts=None, meta=None):
        metric = '{}_max_views_disk_size_{}'.format(
            self.test_config.name, self.cluster_spec.name
        )
        if meta:
            metric = '{}_{}'.format(metric, meta.split()[0].lower())
        title = 'Max. views disk size (GB)'
        if meta:
            title = '{}, {}'.format(title, meta)
        title = '{}, {}'.format(title, self.metric_title)
        title = title.replace(' (min)', '')  # rebalance tests
        metric_info = self._get_metric_info(title)

        query_params = self._get_query_params('max_couch_views_actual_disk_size',
                                              from_ts, to_ts)

        disk_size = 0
        for bucket in self.test_config.buckets:
            db = 'ns_server{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].query(query_params)
            disk_size += round(data.values()[0][0] / 1024 ** 3, 2)  # -> GB

        return disk_size, metric, metric_info

    def calc_mem_used(self, max_min='max'):
        metric = '{}_{}_mem_used_{}'.format(
            self.test_config.name, max_min, self.cluster_spec.name
        )
        title = '{}. mem_used (MB), {}'.format(max_min.title(),
                                               self.metric_title)
        metric_info = self._get_metric_info(title)

        query_params = self._get_query_params('max_mem_used')

        mem_used = []
        for bucket in self.test_config.buckets:
            db = 'ns_server{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].query(query_params)

            mem_used.append(
                round(data.values()[0][0] / 1024 ** 2)  # -> MB
            )
        mem_used = eval(max_min)(mem_used)

        return mem_used, metric, metric_info

    def calc_max_beam_rss(self):
        metric = 'beam_rss_max_{}_{}'.format(self.test_config.name,
                                             self.cluster_spec.name)
        title = 'Max. beam.smp RSS (MB), {}'.format(self.metric_title)
        metric_info = self._get_metric_info(title)

        query_params = self._get_query_params('max_beam.smp_rss')

        max_rss = 0
        for cluster_name, servers in self.cluster_spec.yield_clusters():
            cluster = filter(lambda name: name.startswith(cluster_name),
                             self.cluster_names)[0]
            for server in servers:
                hostname = server.split(':')[0].replace('.', '')
                db = 'atop{}{}'.format(cluster, hostname)  # Legacy
                data = self.seriesly[db].query(query_params)
                rss = round(data.values()[0][0] / 1024 ** 2)
                max_rss = max(max_rss, rss)

        return max_rss, metric, metric_info

    def calc_max_memcached_rss(self):
        metric = '{}_{}_memcached_rss'.format(self.test_config.name,
                                              self.cluster_spec.name)
        title = 'Max. memcached RSS (MB),{}'.format(
            self.metric_title.split(',')[-1]
        )
        metric_info = self._get_metric_info(title)

        query_params = self._get_query_params('max_memcached_rss')

        max_rss = 0
        for (cluster_name, servers), initial_nodes in zip(
                self.cluster_spec.yield_clusters(),
                self.test_config.cluster.initial_nodes,
        ):
            cluster = filter(lambda name: name.startswith(cluster_name),
                             self.cluster_names)[0]
            for server in servers[:initial_nodes]:
                hostname = server.split(':')[0].replace('.', '')
                db = 'atop{}{}'.format(cluster, hostname)
                data = self.seriesly[db].query(query_params)
                rss = round(data.values()[0][0] / 1024 ** 2)
                max_rss = max(max_rss, rss)

        return max_rss, metric, metric_info

    def calc_avg_memcached_rss(self):
        metric = '{}_{}_avg_memcached_rss'.format(self.test_config.name,
                                                  self.cluster_spec.name)
        title = 'Avg. memcached RSS (MB),{}'.format(
            self.metric_title.split(',')[-1]
        )
        metric_info = self._get_metric_info(title)

        query_params = self._get_query_params('avg_memcached_rss')

        rss = list()
        for (cluster_name, servers), initial_nodes in zip(
                self.cluster_spec.yield_clusters(),
                self.test_config.cluster.initial_nodes,
        ):
            cluster = filter(lambda name: name.startswith(cluster_name),
                             self.cluster_names)[0]
            for server in servers[:initial_nodes]:
                hostname = server.split(':')[0].replace('.', '')
                db = 'atop{}{}'.format(cluster, hostname)
                data = self.seriesly[db].query(query_params)
                rss.append(round(data.values()[0][0] / 1024 ** 2))

        avg_rss = sum(rss) / len(rss)
        return avg_rss, metric, metric_info

    def get_indexing_meta(self, value, index_type):
        metric = '{}_{}_{}'.format(self.test_config.name,
                                   index_type.lower(),
                                   self.cluster_spec.name)
        title = '{} index (min), {}'.format(index_type,
                                            self.metric_title)
        metric_info = self._get_metric_info(title)

        return value, metric, metric_info

    def calc_compaction_speed(self, time_elapsed, bucket=True):
        if bucket:
            max_query_params = \
                self._get_query_params('max_couch_docs_actual_disk_size')
            min_query_params = \
                self._get_query_params('min_couch_docs_actual_disk_size')
        else:
            max_query_params = \
                self._get_query_params('max_couch_views_actual_disk_size')
            min_query_params = \
                self._get_query_params('min_couch_views_actual_disk_size')

        max_diff = 0
        for bucket in self.test_config.buckets:
            db = 'ns_server{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].query(max_query_params)
            disk_size_before = data.values()[0][0]

            db = 'ns_server{}{}'.format(self.cluster_names[0], bucket)
            data = self.seriesly[db].query(min_query_params)
            disk_size_after = data.values()[0][0]

            max_diff = max(max_diff, disk_size_before - disk_size_after)

        diff = max_diff / 1024 ** 2 / time_elapsed  # Mbytes/sec
        return round(diff, 1)

    def failover_time(self, reporter):
        metric = '{}_{}_failover'.format(self.test_config.name,
                                         self.cluster_spec.name)
        _split = self.metric_title.split(', ')

        title = 'Graceful failover (min), {}, {}'.format(
            _split[1][-1] + _split[1][1:-1] + _split[1][0],
            ', '.join(_split[2:]))
        metric_info = self._get_metric_info(title, larger_is_better=False)

        rebalance_time = reporter.finish('Failover')

        return rebalance_time, metric, metric_info

    @property
    def calc_network_bandwidth(self):
        self.remote = RemoteHelper(self.cluster_spec, self.test_config, verbose=True)
        for cluster_name, servers in self.cluster_spec.yield_clusters():
            self.in_bytes_transfer += [self.remote.read_bandwidth_stats("to", servers)]
            self.out_bytes_transfer += [self.remote.read_bandwidth_stats("from", servers)]
        logger.info('in bytes', self.in_bytes_transfer)
        logger.info('out bytes', self.out_bytes_transfer)
        return OrderedDict((
            ('in bytes', sum(self.in_bytes_transfer[0].values())),
            ('out bytes', sum(self.out_bytes_transfer[0].values()))))

    @property
    def calc_network_throughput(self):
        in_bytes_per_sec = []
        out_bytes_per_sec = []
        for cluster_name, servers in self.cluster_spec.yield_clusters():
            cluster = filter(lambda name: name.startswith(cluster_name),
                             self.cluster_names)[0]
            for server in servers:
                hostname = server.split(':')[0].replace('.', '')
                db = 'net{}{}'.format(cluster, hostname)
                data = self.seriesly[db].get_all()
                in_bytes_per_sec += [
                    v['in_bytes_per_sec'] for v in data.values()
                ]
                out_bytes_per_sec += [
                    v['out_bytes_per_sec'] for v in data.values()
                ]
        # To prevent exception when the values may not be available during code debugging
        if not in_bytes_per_sec:
            in_bytes_per_sec.append(0)
        if not out_bytes_per_sec:
            out_bytes_per_sec.append(0)
        f = lambda v: format(int(v), ',d')
        return OrderedDict((
            ('min in_bytes  per sec', f(min(in_bytes_per_sec))),
            ('max in_bytes  per sec', f(max(in_bytes_per_sec))),
            ('avg in_bytes  per sec', f(np.mean(in_bytes_per_sec))),
            ('p50 in_bytes  per sec', f(np.percentile(in_bytes_per_sec, 50))),
            ('p95 in_bytes  per sec', f(np.percentile(in_bytes_per_sec, 95))),
            ('p99 in_bytes  per sec', f(np.percentile(in_bytes_per_sec, 99))),
            ('min out_bytes per sec', f(min(out_bytes_per_sec))),
            ('max out_bytes per sec', f(max(out_bytes_per_sec))),
            ('avg out_bytes per sec', f(np.mean(out_bytes_per_sec))),
            ('p50 out_bytes per sec', f(np.percentile(out_bytes_per_sec, 50))),
            ('p95 out_bytes per sec', f(np.percentile(out_bytes_per_sec, 95))),
            ('p99 out_bytes per sec', f(np.percentile(out_bytes_per_sec, 99))),
        ))
