import csv
import os.path

from decorator import decorator
from logger import logger

from six.moves.configparser import (
    NoOptionError,
    NoSectionError,
    SafeConfigParser,
)

REPO = 'https://github.com/couchbase/perfrunner'


@decorator
def safe(method, *args, **kargs):
    try:
        return method(*args, **kargs)
    except (NoSectionError, NoOptionError) as e:
        logger.warn('Failed to get option from config: {}'.format(e))


class Config(object):

    def __init__(self):
        self.config = SafeConfigParser()
        self.name = ''

    def parse(self, fname, override):
        if override:
            override = [x for x in csv.reader(
                ' '.join(override).split(','), delimiter='.')]

        logger.info('Reading configuration file: {}'.format(fname))
        if not os.path.isfile(fname):
            logger.interrupt('File doesn\'t exist: {}'.format(fname))
        self.config.optionxform = str
        self.config.read(fname)
        for section, option, value in override:
            if not self.config.has_section(section):
                self.config.add_section(section)
            self.config.set(section, option, value)

        basename = os.path.basename(fname)
        self.name = os.path.splitext(basename)[0]

    @safe
    def _get_options_as_dict(self, section):
        if section in self.config.sections():
            return {p: v for p, v in self.config.items(section)}
        else:
            return {}


class ClusterSpec(Config):

    @safe
    def yield_clusters(self):
        for cluster_name, servers in self.config.items('clusters'):
            yield cluster_name, [s.split(',', 1)[0] for s in servers.split()]

    @safe
    def yield_masters(self):
        for _, servers in self.yield_clusters():
            yield servers[0]

    @safe
    def yield_servers(self):
        for _, servers in self.yield_clusters():
            for server in servers:
                yield server

    @safe
    def yield_hostnames(self):
        for _, servers in self.yield_clusters():
            for server in servers:
                yield server.split(':')[0]

    @safe
    def yield_servers_by_role(self, role):
        for name, servers in self.config.items('clusters'):
            has_service = []
            for server in servers.split():
                if role in server.split(',')[1:]:
                    has_service.append(server.split(',')[0])
            yield name, has_service

    @safe
    def yield_kv_servers(self):
        for name, servers in self.config.items('clusters'):
            for server in servers.split():
                if ',' in server:
                    continue
                else:
                    yield server.split(':')[0]

    @property
    @safe
    def roles(self):
        server_roles = {}
        for _, node in self.config.items('clusters'):
            for server in node.split():
                name = server.split(',', 1)[0]
                if ',' in server:
                    server_roles[name] = server.split(',', 1)[1]
                else:  # For backward compatibility, set to kv if not specified
                    server_roles[name] = 'kv'

        return server_roles

    @property
    @safe
    def workers(self):
        return self.config.get('clients', 'hosts').split()

    @property
    @safe
    def client_credentials(self):
        return self.config.get('clients', 'credentials').split(':')

    @property
    @safe
    def paths(self):
        data_path = self.config.get('storage', 'data')
        index_path = self.config.get('storage', 'index')
        return data_path, index_path

    @property
    @safe
    def rest_credentials(self):
        return self.config.get('credentials', 'rest').split(':')

    @property
    @safe
    def ssh_credentials(self):
        return self.config.get('credentials', 'ssh').split(':')

    @property
    def parameters(self):
        return self._get_options_as_dict('parameters')


class TestConfig(Config):

    @property
    def test_case(self):
        options = self._get_options_as_dict('test_case')
        return TestCaseSettings(options)

    @property
    def cluster(self):
        options = self._get_options_as_dict('cluster')
        return ClusterSettings(options)

    @property
    def bucket(self):
        options = self._get_options_as_dict('bucket')
        return BucketSettings(options)

    @property
    def bucket_extras(self):
        return self._get_options_as_dict('bucket_extras')

    @property
    def buckets(self):
        return [
            'bucket-{}'.format(i + 1) for i in range(self.cluster.num_buckets)
        ]

    @property
    def compaction(self):
        options = self._get_options_as_dict('compaction')
        return CompactionSettings(options)

    @property
    def restore_settings(self):
        options = self._get_options_as_dict('restore')
        return RestoreSettings(options)

    @property
    def load_settings(self):
        options = self._get_options_as_dict('load')
        return LoadSettings(options)

    @property
    def hot_load_settings(self):
        options = self._get_options_as_dict('hot_load')
        hot_load = HotLoadSettings(options)

        load = self.load_settings
        hot_load.doc_gen = load.doc_gen
        hot_load.doc_partitions = load.doc_partitions
        hot_load.size = load.size
        return hot_load

    @property
    def xdcr_settings(self):
        options = self._get_options_as_dict('xdcr')
        return XDCRSettings(options)

    @property
    def index_settings(self):
        options = self._get_options_as_dict('index')
        return IndexSettings(options)

    @property
    def secondaryindex_settings(self):
        options = self._get_options_as_dict('secondary')
        return SecondaryIndexSettings(options)

    @property
    def n1ql_settings(self):
        options = self._get_options_as_dict('n1ql')
        return N1QLSettings(options)

    @property
    def backup_settings(self):
        options = self._get_options_as_dict('backup')
        return BackupSettings(options)

    @property
    def access_settings(self):
        options = self._get_options_as_dict('access')
        access = AccessSettings(options)
        access.resolve_subcategories(self)

        load = self.load_settings
        access.doc_gen = load.doc_gen
        access.doc_partitions = load.doc_partitions
        access.size = load.size
        options = self._get_options_as_dict('subdoc')
        if options:
            SubDocSettings(options, access)
        return access

    @property
    def rebalance_settings(self):
        options = self._get_options_as_dict('rebalance')
        return RebalanceSettings(options)

    @property
    def stats_settings(self):
        options = self._get_options_as_dict('stats')
        return StatsSettings(options)

    @property
    def internal_settings(self):
        return self._get_options_as_dict('internal')

    @property
    def xdcr_cluster_settings(self):
        return self._get_options_as_dict('xdcr_cluster')

    @property
    def fts_settings(self):
        options = self._get_options_as_dict('fts')
        return FtsSettings(options)

    @property
    def ycsb_settings(self):
        options = self._get_options_as_dict('ycsb')
        return YcsbSettings(options)

    @property
    def dailyp_settings(self):
        options = self._get_options_as_dict('dailyp')
        return DailypSettings(options)

    def get_n1ql_query_definition(self, query_name):
        return self._get_options_as_dict('n1ql-{}'.format(query_name))

    @property
    def fio(self):
        return self._get_options_as_dict('fio')


class TestCaseSettings(object):

    USE_WORKERS = 1

    def __init__(self, options):
        self.test_module = '.'.join(options.get('test').split('.')[:-1])
        self.test_class = options.get('test').split('.')[-1]
        self.test_summary = options.get('summary')
        self.metric_title = options.get('title')
        self.larger_is_better = options.get('larger_is_better')
        self.fts_server = options.get('fts', False)
        self.use_workers = int(options.get('use_workers', self.USE_WORKERS))


class ClusterSettings(object):

    GROUP_NUMBER = 1
    NUM_BUCKETS = 1
    INDEX_MEM_QUOTA = 256
    FTS_INDEX_MEM_QUOTA = 512

    def __init__(self, options):
        self.mem_quota = int(options.get('mem_quota'))
        self.index_mem_quota = int(options.get('index_mem_quota', self.INDEX_MEM_QUOTA))
        self.fts_index_mem_quota = int(options.get('fts_index_mem_quota', self.FTS_INDEX_MEM_QUOTA))
        self.initial_nodes = [
            int(nodes) for nodes in options.get('initial_nodes').split()
        ]
        self.num_buckets = int(options.get('num_buckets', self.NUM_BUCKETS))
        self.num_vbuckets = options.get('num_vbuckets')
        self.group_number = int(options.get('group_number', self.GROUP_NUMBER))


class StatsSettings(object):

    CBMONITOR = {'host': 'cbmonitor.sc.couchbase.com', 'password': 'password'}
    ENABLED = 1
    ADD_SNAPSHOTS = 1
    POST_TO_SF = 0
    INTERVAL = 5
    SECONDARY_STATSFILE = '/root/statsfile'
    LAT_INTERVAL = 1
    POST_RSS = 0
    POST_CPU = 0
    SERIESLY = {'host': 'cbmonitor.sc.couchbase.com'}
    SHOWFAST = {'host': 'cbmonitor.sc.couchbase.com', 'password': 'password'}

    def __init__(self, options):
        self.cbmonitor = {'host': options.get('cbmonitor_host',
                                              self.CBMONITOR['host']),
                          'password': options.get('cbmonitor_password',
                                                  self.CBMONITOR['password'])}
        self.add_snapshots = int(options.get('add_snapshots', self.ADD_SNAPSHOTS))
        self.enabled = int(options.get('enabled', self.ENABLED))
        self.post_to_sf = int(options.get('post_to_sf', self.POST_TO_SF))
        self.interval = int(options.get('interval', self.INTERVAL))
        self.lat_interval = float(options.get('lat_interval', self.LAT_INTERVAL))
        self.secondary_statsfile = options.get('secondary_statsfile', self.SECONDARY_STATSFILE)
        self.post_rss = int(options.get('post_rss', self.POST_RSS))
        self.post_cpu = int(options.get('post_cpu', self.POST_CPU))
        self.seriesly = {'host': options.get('seriesly_host',
                                             self.SERIESLY['host'])}
        self.showfast = {'host': options.get('showfast_host',
                                             self.SHOWFAST['host']),
                         'password': options.get('showfast_password',
                                                 self.SHOWFAST['password'])}


class BucketSettings(object):

    PASSWORD = 'password'
    REPLICA_NUMBER = 1
    REPLICA_INDEX = 0
    EVICTION_POLICY = 'valueOnly'  # alt: fullEviction
    TIME_SYNCHRONIZATION = None  # alt: enabledWithDrift, enabledWithoutDrift

    def __init__(self, options):
        self.password = options.get('password', self.PASSWORD)
        self.replica_number = int(
            options.get('replica_number', self.REPLICA_NUMBER)
        )
        self.replica_index = int(
            options.get('replica_index', self.REPLICA_INDEX)
        )
        self.eviction_policy = \
            options.get('eviction_policy', self.EVICTION_POLICY)

        self.time_synchronization = \
            options.get('time_synchronization', self.TIME_SYNCHRONIZATION)


class CompactionSettings(object):

    DB_PERCENTAGE = 30
    VIEW_PERCENTAGE = 30
    PARALLEL = True

    def __init__(self, options):
        self.db_percentage = options.get('db_percentage', self.DB_PERCENTAGE)
        self.view_percentage = options.get('view_percentage', self.VIEW_PERCENTAGE)
        self.parallel = options.get('parallel', self.PARALLEL)

    def __str__(self):
        return str(self.__dict__)


class TargetSettings(object):

    def __init__(self, host_port, bucket, password, prefix):
        self.password = password
        self.node = host_port
        self.bucket = bucket
        self.prefix = prefix


class RebalanceSettings(object):

    SWAP = 0  # Don't swap by default
    FAILOVER = 0  # No failover by default
    GRACEFUL_FAILOVER = 0
    DELTA_RECOVERY = 0  # Full recovery by default
    SLEEP_AFTER_FAILOVER = 600
    START_AFTER = 1200
    STOP_AFTER = 1200

    def __init__(self, options):
        self.nodes_after = [int(_) for _ in options.get('nodes_after').split()]
        self.swap = int(options.get('swap', self.SWAP))
        self.failover = int(options.get('failover', self.FAILOVER))
        self.graceful_failover = int(options.get('graceful_failover',
                                                 self.GRACEFUL_FAILOVER))
        self.sleep_after_failover = int(options.get('sleep_after_failover',
                                                    self.SLEEP_AFTER_FAILOVER))
        self.delta_recovery = int(options.get('delta_recovery',
                                              self.DELTA_RECOVERY))
        self.start_after = int(options.get('start_after', self.START_AFTER))
        self.stop_after = int(options.get('stop_after', self.STOP_AFTER))


class PhaseSettings(object):

    CREATES = 0
    READS = 0
    UPDATES = 0
    DELETES = 0
    CASES = 0
    OPS = 0
    THROUGHPUT = float('inf')
    QUERY_THROUGHPUT = float('inf')
    N1QL_THROUGHPUT = float('inf')
    N1QL_THROUGHPUT_MAX = float('inf')

    DOC_GEN = 'old'
    DOC_PARTITIONS = 1

    ITEMS = 0
    EXISTING_ITEMS = 0
    SIZE = 2048
    EXPIRATION = 0
    WORKING_SET = 100
    WORKING_SET_ACCESS = 100

    WORKERS = 0
    QUERY_WORKERS = 0
    N1QL_WORKERS = 0
    N1QL_OP = 'read'
    SPRING_WORKERS = 100

    SEQ_READS = False
    SEQ_UPDATES = False

    TIME = 3600 * 24

    ASYNC = False
    PARALLEL_WORKLOAD = False

    ITERATIONS = 1

    def __init__(self, options):
        self.creates = int(options.get('creates', self.CREATES))
        self.reads = int(options.get('reads', self.READS))
        self.updates = int(options.get('updates', self.UPDATES))
        self.deletes = int(options.get('deletes', self.DELETES))
        self.cases = int(options.get('cases', self.CASES))
        self.ops = float(options.get('ops', self.OPS))
        self.throughput = float(options.get('throughput', self.THROUGHPUT))
        self.query_throughput = float(options.get('query_throughput',
                                                  self.QUERY_THROUGHPUT))
        self.n1ql_throughput = float(options.get('n1ql_throughput',
                                                 self.N1QL_THROUGHPUT))
        self.n1ql_throughput_max = float(options.get('n1ql_throughput_max',
                                                     self.N1QL_THROUGHPUT_MAX))

        self.doc_gen = options.get('doc_gen', self.DOC_GEN)
        self.doc_partitions = int(options.get('doc_partitions',
                                              self.DOC_PARTITIONS))
        self.size = int(options.get('size', self.SIZE))
        self.items = int(options.get('items', self.ITEMS))
        self.existing_items = int(options.get('existing_items', self.EXISTING_ITEMS))
        self.expiration = int(options.get('expiration', self.EXPIRATION))
        self.working_set = float(options.get('working_set', self.WORKING_SET))
        self.working_set_access = int(options.get('working_set_access',
                                                  self.WORKING_SET_ACCESS))

        self.workers = int(options.get('workers', self.WORKERS))

        self.query_workers = int(options.get('query_workers',
                                             self.QUERY_WORKERS))
        self.n1ql_workers = int(options.get('n1ql_workers',
                                            self.N1QL_WORKERS))
        self.subdoc_workers = 0
        self.n1ql_op = options.get('n1ql_op', self.N1QL_OP)
        self.spring_workers = int(options.get('spring_workers', self.SPRING_WORKERS))
        self.n1ql_queries = []
        if 'n1ql_queries' in options:
            self.n1ql_queries = options.get('n1ql_queries').strip().split(',')

        self.seq_reads = self.SEQ_READS
        self.seq_updates = self.SEQ_UPDATES

        self.ddocs = None
        self.index_type = None
        self.qparams = {}

        self.n1ql = None
        self.time = int(options.get('time', self.TIME))

        self.async = bool(int(options.get('async', self.ASYNC)))
        self.parallel_workload = bool(int(options.get('parallel_workload', self.PARALLEL_WORKLOAD)))
        self.iterations = int(options.get('iterations', self.ITERATIONS))

        self.filename = None
        self.fts_config = None
        self.operations = bool(int(options.get('operations', False)))

    def resolve_subcategories(self, config):
        subcategories = self.n1ql_queries
        query_specs = []
        for subcategory in subcategories:
            query_specs.append(config.get_n1ql_query_definition(subcategory))
        self.n1ql_queries = query_specs

    def __str__(self):
        return str(self.__dict__)


class LoadSettings(PhaseSettings):

    CREATES = 100
    SEQ_UPDATES = True


class HotLoadSettings(PhaseSettings):

    SEQ_READS = True
    SEQ_UPDATES = False

    def __init__(self, options):
        if 'size' in options:
            logger.interrupt(
                "The document `size` may only be set in the [load] "
                "and not in the [hot_load] section")

        super(HotLoadSettings, self).__init__(options)


class RestoreSettings(object):

    SNAPSHOT = None

    def __init__(self, options):
        self.snapshot = options.get('snapshot', self.SNAPSHOT)

    def __str__(self):
        return str(self.__dict__)


class XDCRSettings(object):

    XDCR_REPLICATION_TYPE = 'bidir'
    XDCR_REPLICATION_PROTOCOL = None
    XDCR_USE_SSL = False
    WAN_ENABLED = False
    FILTER_EXPRESSION = None

    def __init__(self, options):
        self.replication_type = options.get('replication_type',
                                            self.XDCR_REPLICATION_TYPE)
        self.replication_protocol = options.get('replication_protocol',
                                                self.XDCR_REPLICATION_PROTOCOL)
        self.use_ssl = int(options.get('use_ssl', self.XDCR_USE_SSL))
        self.wan_enabled = int(options.get('wan_enabled', self.WAN_ENABLED))
        self.filter_expression = options.get('filter_expression', self.FILTER_EXPRESSION)

    def __str__(self):
        return str(self.__dict__)


class IndexSettings(object):

    VIEWS = '[1]'
    DISABLED_UPDATES = 0
    PARAMS = '{}'

    def __init__(self, options):
        self.views = eval(options.get('views', self.VIEWS))
        self.params = eval(options.get('params', self.PARAMS))
        self.disabled_updates = int(options.get('disabled_updates',
                                                self.DISABLED_UPDATES))
        self.index_type = options.get('index_type')

    def __str__(self):
        return str(self.__dict__)


class SecondaryIndexSettings(object):

    NAME = 'noname'
    FIELD = 'nofield'
    DB = ''
    STALE = 'true'
    CBINDEXPERF_CONFIGFILE = ''
    INIT_NUM_CONNECTIONS = 0
    STEP_NUM_CONNECTIONS = 0
    MAX_NUM_CONNECTIONS = 0

    def __init__(self, options):
        self.name = options.get('name', self.NAME)
        self.field = options.get('field', self.FIELD)
        self.db = options.get('db', self.DB)
        self.stale = options.get('stale', self.STALE)
        self.indexes = self.name.split(",") if self.name is not self.NAME else []
        self.cbindexperf_configfile = options.get('cbindexperf_configfile', self.CBINDEXPERF_CONFIGFILE)
        self.init_num_connections = int(options.get('init_num_connections', self.INIT_NUM_CONNECTIONS))
        self.step_num_connections = int(options.get('step_num_connections', self.STEP_NUM_CONNECTIONS))
        self.max_num_connections = int(options.get('max_num_connections', self.MAX_NUM_CONNECTIONS))
        for name in self.name.split(","):
            index_partition_name = "index_{}_partitions".format(name)
            val = str(options.get(index_partition_name, ''))
            if val:
                setattr(self, index_partition_name, val)

        self.settings = {}

        for option in options:
            if option.startswith('indexer') or \
               option.startswith('projector') or \
               option.startswith('queryport.client.settings'):

                value = options.get(option)
                try:
                    if '.' in value:
                        self.settings[option] = float(value)
                    else:
                        self.settings[option] = int(value)
                    continue
                except:
                    pass

                self.settings[option] = value

    def __str__(self):
        return str(self.__dict__)


class N1QLSettings(object):

    def __init__(self, options):
        self.indexes = []
        if 'indexes' in options:
            self.indexes = options.get('indexes').strip().split('\n')

        self.settings = {}
        for option in options:
            if option.startswith('query.settings'):
                key = option.split('.')[2]
                value = options.get(option)
                try:
                    if '.' in value:
                        self.settings[key] = float(value)
                    else:
                        self.settings[key] = int(value)
                    continue
                except:
                    pass

                self.settings[key] = value

    def __str__(self):
        return str(self.__dict__)


class SubDocSettings(object):

    SUBDOC_WORKERS = 0
    SUBDOC_FIELDS = []
    SUBDOC_COUNTER_FIELDS = []
    SUBDOC_DELETE_FIELDS = []

    def __init__(self, options, access):
        access.subdoc_workers = int(options.get('workers'), self.SUBDOC_WORKERS)
        access.subdoc_fields = options.get(('fields'), self.SUBDOC_FIELDS)
        access.subdoc_counter_fields = options.get(('counter_fields'), self.SUBDOC_COUNTER_FIELDS)
        access.subdoc_delete_fields = options.get(('delete_fields'), self.SUBDOC_DELETE_FIELDS)


class AccessSettings(PhaseSettings):

    OPS = float('inf')

    def __init__(self, options):
        if 'size' in options:
            logger.interrupt(
                "The document `size` may only be set in the [load] "
                "and not in the [access] section")

        super(AccessSettings, self).__init__(options)

    @property
    def fts_settings(self):
        options = self._get_options_as_dict('fts')
        return FtsSettings(options)

    def __str__(self):
        return str(self.__dict__)


class BackupSettings(object):

    COMPRESSION = False

    def __init__(self, options):
        self.compression = int(options.get('compression', self.COMPRESSION))


class FtsSettings(object):
    def __init__(self, options):
        self.port = int(options.get("port", 0))
        self.name = options.get("name")
        self.items = int(options.get("items", 0))
        self.worker = int(options.get("worker", 0))
        self.query = options.get("query", '')
        self.query_size = int(options.get("query_size", 10))
        self.throughput = 0
        self.elastic = bool(int(options.get("elastic", 0)))
        self.query_file = options.get("query_file", None)
        self.type = options.get("type", "match")
        self.logfile = options.get("logfile", None)
        self.orderby = options.get("orderby", None)
        self.storage = options.get("backup_path")
        self.repo = options.get("repo_path")
        self.field = options.get("field", None)
        self.index_configfile = options.get("index_configfile", None)

    def __str__(self):
        return str(self.__dict__)


class YcsbSettings(object):
    def __init__(self, options):
        self.sdk = options.get("sdk")
        self.bucket = options.get("bucket")
        self.jvm = options.get("jvm-args")
        self.threads = options.get("threads")
        self.parameters = options.get("parameters")
        self.workload = options.get("workload_path")
        self.size = options.get("size")
        self.reccount = options.get("recordcount")
        self.opcount = options.get("operationcount")
        self.path = options.get("path")
        self.log_file = options.get("export_file")
        self.log_path = options.get("export_file_path")
        self.index = options.get("index")

    def __str__(self):
        return str(self.__dict__)


class DailypSettings(object):
    def __init__(self, options):
        self.category = options.get("dailyp_category")
        self.subcategory = options.get("dailyp_subcategory")
        self.threshold = int(options.get("threshold"))

    def __str__(self):
        return str(self.__dict__)
