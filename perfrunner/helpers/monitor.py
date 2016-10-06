import time

from logger import logger

from perfrunner.helpers import misc
from perfrunner.helpers.rest import RestHelper


class Monitor(RestHelper):
    POLLING_INTERVAL = 2
    POLLING_INTERVAL_INDEXING = 1
    MAX_RETRY = 60
    REBALANCE_TIMEOUT = 3600 * 2

    DISK_QUEUES = (
        'ep_queue_size',
        'ep_flusher_todo',
        'ep_diskqueue_items',
        'vb_active_queue_size',
        'vb_replica_queue_size',
    )
    TAP_QUEUES = (
        'ep_tap_replica_qlen',
        'ep_tap_replica_queue_itemondisk',
        'ep_tap_rebalance_queue_backfillremaining',
    )

    UPR_QUEUES = (
        'ep_upr_replica_items_remaining',
        'ep_upr_other_items_remaining',
    )

    XDCR_QUEUES = (
        'replication_changes_left',
    )

    def monitor_rebalance(self, host_port):
        logger.info('Monitoring rebalance status')

        is_running = True
        last_progress = 0
        last_progress_time = time.time()
        while is_running:
            time.sleep(self.POLLING_INTERVAL)

            is_running, progress = self.get_rebalance_status(host_port)
            if progress == last_progress:
                if time.time() - last_progress_time > self.REBALANCE_TIMEOUT:
                    logger.error('Rebalance hung')
                    break
            else:
                last_progress = progress
                last_progress_time = time.time()

            if progress is not None:
                logger.info('Rebalance progress: {} %'.format(progress))

        logger.info('Rebalance completed')

    def _wait_for_empty_queues(self, host_port, bucket, queues, stats_function=None):
        metrics = list(queues)
        while metrics:
            if stats_function:
                bucket_stats = stats_function(host_port, bucket)
            else:
                bucket_stats = self.get_bucket_stats(host_port, bucket)
            # As we are changing metrics in the loop; take a copy of
            # it to iterate over.
            for metric in list(metrics):
                stats = bucket_stats['op']['samples'].get(metric)
                if stats:
                    last_value = stats[-1]
                    if last_value:
                        logger.info('{} = {:,}'.format(metric, last_value))
                        continue
                    else:
                        logger.info('{} reached 0'.format(metric))
                metrics.remove(metric)
            if metrics:
                time.sleep(self.POLLING_INTERVAL)

    def monitor_disk_queues(self, host_port, bucket):
        logger.info('Monitoring disk queues: {}'.format(bucket))
        self._wait_for_empty_queues(host_port, bucket, self.DISK_QUEUES)

    def monitor_tap_queues(self, host_port, bucket):
        logger.info('Monitoring TAP queues: {}'.format(bucket))
        self._wait_for_empty_queues(host_port, bucket, self.TAP_QUEUES)

    def monitor_upr_queues(self, host_port, bucket):
        logger.info('Monitoring UPR queues: {}'.format(bucket))
        self._wait_for_empty_queues(host_port, bucket, self.UPR_QUEUES)

    def monitor_xdcr_queues(self, host_port, bucket):
        logger.info('Monitoring XDCR queues: {}'.format(bucket))
        # MB-14366: XDCR stats endpoint changed in 4.0
        if self.check_rest_endpoint_exists("http://{}/pools/default/buckets/@xdcr-{}/stats"
                                           .format(host_port, bucket)):
            stats_function = self.get_goxdcr_stats
        else:
            # Use default stats function for older builds.
            stats_function = None
        self._wait_for_empty_queues(host_port, bucket, self.XDCR_QUEUES,
                                    stats_function)

    def monitor_task(self, host_port, task_type):
        logger.info('Monitoring task: {}'.format(task_type))

        while True:
            time.sleep(self.POLLING_INTERVAL)

            tasks = [task for task in self.get_tasks(host_port)
                     if task.get('type') == task_type]
            if tasks:
                for task in tasks:
                    logger.info('{}: {}%, bucket: {}, ddoc: {}'.format(
                        task_type, task.get('progress'),
                        task.get('bucket'), task.get('designDocument')
                    ))
            else:
                break
        logger.info('Task {} successfully completed'.format(task_type))

    def monitor_warmup(self, memcached, host_port, bucket):
        logger.info('Monitoring warmup status: {}@{}'.format(bucket,
                                                             host_port))

        host = host_port.split(':')[0]
        # The supplied memcached_port may not be used if authless bucket is
        # used due to FTS testing. See helpers/memcached.py and the actual
        # get_stats call.
        memcached_port = self.get_memcached_port(host_port)
        while True:
            stats = memcached.get_stats(host, memcached_port, bucket, 'warmup')
            if 'ep_warmup_state' in stats:
                state = stats['ep_warmup_state']
                if state == 'done':
                    return float(stats.get('ep_warmup_time', 0))
                else:
                    logger.info('Warmpup status: {}'.format(state))
                    time.sleep(self.POLLING_INTERVAL)
            else:
                    logger.info('No warmup stats are available, continue polling')
                    time.sleep(self.POLLING_INTERVAL)

    def monitor_node_health(self, host_port):
        logger.info('Monitoring node health')

        for retry in range(self.MAX_RETRY):
            unhealthy_nodes = {
                n for n, status in self.node_statuses(host_port).items()
                if status != 'healthy'
            } | {
                n for n, status in self.node_statuses_v2(host_port).items()
                if status != 'healthy'
            }
            if unhealthy_nodes:
                time.sleep(self.POLLING_INTERVAL)
            else:
                break
        else:
            logger.interrupt('Some nodes are not healthy: {}'.format(
                unhealthy_nodes
            ))

    def monitor_index_state(self, host, index_name=None):
        logger.info('Monitoring index state')

        if index_name is not None:
            statement = 'SELECT * FROM system:indexes'
        else:
            statement = 'SELECT * FROM system:indexes WHERE name = "{}"'.format(index_name)

        is_building = True
        while is_building:
            time.sleep(self.POLLING_INTERVAL)

            response = self.exec_n1ql_statement(host, statement)
            if response['status'] == 'success':
                for result in response['results']:
                    if result['indexes']['state'] != 'online':
                        break
                else:
                    is_building = False
            else:
                logger.error(response['status'])

        if index_name is None:
            logger.info('All Indexes: ONLINE')
        else:
            logger.info('Index "{}" is ONLINE'.format(index_name))

    def wait_for_secindex_init_build(self, host, indexes):
        # POLL until initial index build is complete
        logger.info(
            "Waiting for the following indexes to be ready: {}".format(indexes))

        indexes_ready = [0 for i in indexes]

        def get_index_status(json2i, index):
            """
            Return json2i["status"][k]["status"] if json2i["status"][k]["name"]
            matches the desired index.
            """
            for d in json2i["status"]:
                if d["name"] == index:
                    return d["status"]
            return None

        @misc.retry(catch=(KeyError,), iterations=10, wait=30)
        def update_indexes_ready():
            json2i = self.get_index_status(host)
            for i, index in enumerate(indexes):
                status = get_index_status(json2i, index)
                if status == 'Ready':
                    indexes_ready[i] = 1

        init_ts = time.time()
        while sum(indexes_ready) != len(indexes):
            time.sleep(self.POLLING_INTERVAL_INDEXING)
            update_indexes_ready()
        finish_ts = time.time()
        logger.info('secondary index build time: {}'.format(finish_ts - init_ts))
        time_elapsed = round(finish_ts - init_ts)
        return time_elapsed

    def wait_for_secindex_incr_build(self, index_nodes, bucket, indexes, numitems):
        # POLL until incremenal index build is complete
        logger.info('expecting {} num_docs_indexed for indexes {}'.format(numitems, indexes))

        # collect num_docs_indexed information globally from all index nodes
        hosts = [node.split(':')[0] for node in index_nodes]

        def get_num_docs_indexed():
            data = self.get_index_stats(hosts)
            num_indexed = []
            for index in indexes:
                key = "" + bucket + ":" + index + ":num_docs_indexed"
                val = data[key]
                num_indexed.append(val)
            return num_indexed

        def get_num_docs_index_pending():
            data = self.get_index_stats(hosts)
            num_pending = []
            for index in indexes:
                key = "" + bucket + ":" + index + ":num_docs_pending"
                val1 = data[key]
                key = "" + bucket + ":" + index + ":num_docs_queued"
                val2 = data[key]
                val = int(val1) + int(val2)
                num_pending.append(val)
            return num_pending

        expected_num_pending = [0] * len(indexes)
        while True:
            time.sleep(self.POLLING_INTERVAL_INDEXING)
            curr_num_pending = get_num_docs_index_pending()
            if curr_num_pending == expected_num_pending:
                break
        curr_num_indexed = get_num_docs_indexed()
        logger.info("Number of Items indexed {}".format(curr_num_indexed))

    def wait_for_num_connections(self, index_node, expected_connections):
        curr_connections = self.get_index_num_connections(index_node)
        retry = 1
        while curr_connections < expected_connections and retry < self.MAX_RETRY:
            time.sleep(self.POLLING_INTERVAL_INDEXING)
            curr_connections = self.get_index_num_connections(index_node)
            logger.info("Got current connections {}".format(curr_connections))
            retry += 1
        if retry == self.MAX_RETRY:
            return False
        return True
