import time

from logger import logger

from perfrunner.helpers.memcached import MemcachedHelper
from perfrunner.helpers.misc import server_group
from perfrunner.helpers.monitor import Monitor
from perfrunner.helpers.remote import RemoteHelper
from perfrunner.helpers.rest import RestHelper


class ClusterManager(object):

    def __init__(self, cluster_spec, test_config, verbose):
        self.cluster_spec = cluster_spec
        self.test_config = test_config

        self.rest = RestHelper(cluster_spec)
        self.remote = RemoteHelper(cluster_spec, test_config, verbose)
        self.monitor = Monitor(cluster_spec)
        self.memcached = MemcachedHelper(test_config)

        self.clusters = cluster_spec.yield_clusters
        self.servers = cluster_spec.yield_servers
        self.masters = cluster_spec.yield_masters

        self.initial_nodes = test_config.cluster.initial_nodes
        self.mem_quota = test_config.cluster.mem_quota
        self.index_mem_quota = test_config.cluster.index_mem_quota
        self.fts_index_mem_quota = test_config.cluster.fts_index_mem_quota
        self.group_number = test_config.cluster.group_number or 1
        self.roles = cluster_spec.roles

    def set_data_path(self):
        if self.cluster_spec.paths:
            data_path, index_path = self.cluster_spec.paths
            for server in self.servers():
                self.rest.set_data_path(server, data_path, index_path)

    def rename(self):
        for server in self.servers():
            self.rest.rename(server)

    def set_auth(self):
        for server in self.servers():
            self.rest.set_auth(server)

    def set_mem_quota(self):
        for server in self.servers():
            self.rest.set_mem_quota(server, self.mem_quota)

    def set_index_mem_quota(self):
        for server in self.servers():
            self.rest.set_index_mem_quota(server, self.index_mem_quota)

    def set_fts_index_mem_quota(self):
        master_node = self.masters().next()
        version = self.rest.get_version(master_node)
        if version.split('-')[0] < '4.5.0':
            '''
            FTS was introduced in 4.5.0 so any version less
            than will not executed
            '''
            return
        for server in self.servers():
            self.rest.set_fts_index_mem_quota(server, self.fts_index_mem_quota)

    def set_query_settings(self):
        settings = self.test_config.n1ql_settings.settings
        for _, servers in self.cluster_spec.yield_servers_by_role('n1ql'):
            for server in servers:
                self.rest.set_query_settings(server, settings)

    def set_index_settings(self):
        if not list(self.cluster_spec.yield_servers_by_role('index'))[0][1]:
            return

        if self.test_config.secondaryindex_settings.db != 'moi':
            settings = self.test_config.secondaryindex_settings.settings
            for _, servers in self.cluster_spec.yield_servers_by_role('index'):
                for server in servers:
                    self.rest.set_index_settings(server, settings)
            self.remote.restart()
            self.wait_until_healthy()
            time.sleep(60)
        else:
            logger.info("DB type is moi. Not setting the indexer settings. Taking the default indexer settings")

    def set_services(self):
        for (_, servers), initial_nodes in zip(self.clusters(),
                                               self.initial_nodes):
            master = servers[0]
            self.rest.set_services(master, self.roles[master])

    def disable_moxi(self):
        if self.test_config.cluster.disable_moxi is not None:
            self.remote.disable_moxi()

    def create_server_groups(self):
        for master in self.masters():
            for i in range(1, self.group_number):
                name = 'Group {}'.format(i + 1)
                self.rest.create_server_group(master, name=name)

    def add_nodes(self):
        for (_, servers), initial_nodes in zip(self.clusters(),
                                               self.initial_nodes):

            if initial_nodes < 2:  # Single-node cluster
                continue

            # Adding initial nodes
            master = servers[0]
            if self.group_number > 1:
                groups = self.rest.get_server_groups(master)
            else:
                groups = {}
            for i, host_port in enumerate(servers[1:initial_nodes],
                                          start=1):
                uri = groups.get(server_group(servers[:initial_nodes],
                                              self.group_number, i))
                self.rest.add_node(master, host_port, self.roles[host_port],
                                   uri)

            # Rebalance
            master = servers[0]
            known_nodes = servers[:initial_nodes]
            ejected_nodes = []
            self.rest.rebalance(master, known_nodes, ejected_nodes)
            self.monitor.monitor_rebalance(master)
        self.wait_until_healthy()

    def create_buckets(self, empty_buckets=False):
        ram_quota = self.mem_quota / (self.test_config.cluster.num_buckets +
                                      self.test_config.cluster.emptybuckets)
        replica_number = self.test_config.bucket.replica_number
        replica_index = self.test_config.bucket.replica_index
        eviction_policy = self.test_config.bucket.eviction_policy
        threads_number = self.test_config.bucket.threads_number
        proxy_port = self.test_config.bucket.proxy_port
        password = self.test_config.bucket.password
        buckets = self.test_config.emptybuckets if empty_buckets else self.test_config.buckets
        time_synchronization = self.test_config.bucket.time_synchronization

        for master in self.masters():
            for bucket_name in buckets:
                self.rest.create_bucket(host_port=master,
                                        name=bucket_name,
                                        ram_quota=ram_quota,
                                        replica_number=replica_number,
                                        replica_index=replica_index,
                                        eviction_policy=eviction_policy,
                                        threads_number=threads_number,
                                        password=password,
                                        proxy_port=proxy_port,
                                        time_synchronization=time_synchronization)

    def configure_auto_compaction(self):
        compaction_settings = self.test_config.compaction
        for master in self.masters():
            self.rest.configure_auto_compaction(master, compaction_settings)

    def configure_internal_settings(self):
        internal_settings = self.test_config.internal_settings
        for master in self.masters():
            for parameter, value in internal_settings.items():
                self.rest.set_internal_settings(master,
                                                {parameter: int(value)})

    def configure_xdcr_settings(self):
        xdcr_cluster_settings = self.test_config.xdcr_cluster_settings
        for master in self.masters():
            for parameter, value in xdcr_cluster_settings.items():
                self.rest.set_xdcr_cluster_settings(master,
                                                    {parameter: int(value)})

    def tweak_memory(self):
        self.remote.reset_swap()
        self.remote.drop_caches()
        self.remote.set_swappiness()
        self.remote.disable_thp()

    def restart_with_alternative_num_vbuckets(self):
        num_vbuckets = self.test_config.cluster.num_vbuckets
        if num_vbuckets is not None:
            self.remote.restart_with_alternative_num_vbuckets(num_vbuckets)

    def restart_with_alternative_bucket_options(self):
        cmd = 'ns_bucket:update_bucket_props("{}", ' \
              '[{{extra_config_string, "{}={}"}}]).'

        for option in ('defragmenter_enabled',
                       'exp_pager_stime',
                       'ht_locks',
                       'max_num_shards',
                       'max_threads',
                       'warmup_min_memory_threshold',
                       'bfilter_enabled'):
            value = getattr(self.test_config.bucket, option)
            if value != -1 and value is not None:
                logger.info('Changing {} to {}'.format(option, value))
                for master in self.masters():
                    for bucket in self.test_config.buckets:
                        diag_eval = cmd.format(bucket, option, value)
                        self.rest.run_diag_eval(master, diag_eval)
                self.remote.restart()
                self.wait_until_healthy()

    def tune_logging(self):
        self.remote.tune_log_rotation()
        self.remote.restart()

    def restart_with_alternative_num_cpus(self):
        num_cpus = self.test_config.cluster.num_cpus
        if num_cpus:
            self.remote.restart_with_alternative_num_cpus(num_cpus)

    def restart_with_tcmalloc_aggressive_decommit(self):
        if self.test_config.cluster.tcmalloc_aggressive_decommit:
            self.remote.restart_with_tcmalloc_aggressive_decommit()

    def restart_with_sfwi(self):
        if self.test_config.cluster.sfwi:
            self.remote.restart_with_sfwi()

    def enable_auto_failover(self):
        for master in self.masters():
            self.rest.enable_auto_failover(master)

    def wait_until_warmed_up(self):
        for master in self.cluster_spec.yield_masters():
            for bucket in self.test_config.buckets:
                self.monitor.monitor_warmup(self.memcached, master, bucket)

    def wait_until_healthy(self):
        for master in self.cluster_spec.yield_masters():
            self.monitor.monitor_node_health(master)

    def change_watermarks(self):
        watermark_settings = self.test_config.watermark_settings
        for host_port, initial_nodes in zip(self.servers(),
                                            self.initial_nodes):
            host = host_port.split(':')[0]
            memcached_port = self.rest.get_memcached_port(host_port)
            for bucket in self.test_config.buckets:
                for key, val in watermark_settings.items():
                    val = self.memcached.calc_watermark(val, self.mem_quota)
                    self.memcached.set_flusher_param(host, memcached_port,
                                                     bucket, key, val)

    def start_cbq_engine(self):
        if self.test_config.cluster.run_cbq:
            self.remote.start_cbq()

    def change_dcp_io_threads(self):
        if self.test_config.secondaryindex_settings.db == 'moi':
            cmd = 'ns_bucket:update_bucket_props("{}", ' \
                  '[{{extra_config_string, "max_num_auxio=16"}}]).'
            for master in self.masters():
                for bucket in self.test_config.buckets:
                    diag_eval = cmd.format(bucket)
                    self.rest.run_diag_eval(master, diag_eval)
            time.sleep(30)
            self.remote.restart()
            self.wait_until_healthy()
