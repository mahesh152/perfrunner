import json
import time

import requests
from logger import logger
from requests.auth import HTTPBasicAuth

from perfrunner.helpers.cbmonitor import with_stats
from perfrunner.tests import PerfTest


class FTStest(PerfTest):

    """
    The most basic FTS workflow:
        Initial data load ->
            Persistence and intra-cluster replication (for consistency) ->
                Data compaction (for consistency) ->
                    "Hot" load or working set warm up ->
                        "access" phase or active workload
    """

    def __init__(self, cluster_spec, test_config, verbose):
        super(FTStest, self).__init__(cluster_spec, test_config, verbose)

        self.index_definition = self.get_json_from_file(self.test_config.fts_settings.index_configfile)
        self.host_port = [x for x in self.cluster_spec.yield_servers()][0]
        self.host = self.host_port.split(':')[0]
        self.fts_port = 8094
        self.host_port = '{}:{}'.format(self.host, self.fts_port)
        self.fts_index = self.test_config.fts_settings.name
        self.header = {'Content-Type': 'application/json'}
        self.requests = requests.session()
        self.wait_time = 30
        self.fts_doccount = self.test_config.fts_settings.items
        self.prepare_index()
        self.index_time_taken = 0
        self.auth = HTTPBasicAuth('Administrator', 'password')
        self.orderbymetric = self.test_config.fts_settings.orderby

    @staticmethod
    def get_json_from_file(file_name):
        with open(file_name) as fh:
            return json.load(fh)

    @with_stats
    def access(self):
        super(FTStest, self).timer()

    def access_bg_test(self):
        access_settings = self.test_config.access_settings
        access_settings.fts_config = self.test_config.fts_settings
        self.access_bg(access_settings)
        self.access()

    def load(self):
        logger.info('load/restore data to bucket')
        self.remote.cbrestorefts(self.test_config.fts_settings.storage, self.test_config.fts_settings.repo)

    def run(self):
        self.delete_index()
        self.load()
        self.wait_for_persistence()
        self.compact_bucket()
        self.workload = self.test_config.access_settings

    def delete_index(self):
        self.requests.delete(self.index_url,
                             auth=(self.rest.rest_username,
                                   self.rest.rest_password),
                             headers=self.header)

    def prepare_index(self):
        self.index_definition['name'] = self.fts_index
        self.index_definition["sourceName"] = self.test_config.buckets[0]
        self.index_url = "http://{}/api/index/{}".\
            format(self.host_port, self.fts_index)
        logger.info('Created the Index definition : {}'.
                    format(self.index_definition))

    def check_rec_presist(self):
        rec_memory = self.fts_doccount
        self.fts_url = "http://{}/api/nsstats".format(self.host_port)
        key = ':'.join([self.test_config.buckets[0], self.fts_index, 'num_recs_to_persist'])
        while rec_memory != 0:
            logger.info("Record persists to be expected: %s" % rec_memory)
            r = self.requests.get(url=self.fts_url, auth=self.auth)
            time.sleep(self.wait_time)
            rec_memory = r.json()[key]

    def create_index(self):
        r = self.requests.put(self.index_url,
                              data=json.dumps(self.index_definition, ensure_ascii=False),
                              auth=(self.rest.rest_username, self.rest.rest_password),
                              headers=self.header)
        if not r.status_code == 200:
            logger.info("URL: %s" % self.index_url)
            logger.info("data: %s" % self.index_definition)
            logger.info("HEADER: %s" % self.header)
            logger.error(r.text)
            raise RuntimeError("Failed to create FTS index")
        time.sleep(self.wait_time)

    def wait_for_index(self, wait_interval=10, progress_interval=60):
        logger.info(' Waiting for Index to be completed')
        last_reported = time.time()
        lastcount = 0
        retry = 0
        while True and (retry != 6):
            r = self.requests.get(url=self.index_url + '/count', auth=self.auth)
            if not r.status_code == 200:
                raise RuntimeError(
                    "Failed to fetch document count of index. Status {}".format(r.status_code))
            count = int(r.json()['count'])
            if lastcount >= count:
                retry += 1
                time.sleep(wait_interval * retry)
                logger.info('count of documents :{} is same or less for retry {}'.format(count, retry))
                continue
            retry = 0
            logger.info("Done at document count {}".
                        format(count))
            if count >= self.fts_doccount:
                logger.info("Finished at document count {}".
                            format(count))
                return
            check_report = time.time()
            if check_report - last_reported >= progress_interval:
                last_reported = check_report
                logger.info("(progress) Document count is at {}".
                            format(count))
            lastcount = count
        if lastcount != self.fts_doccount:
            raise RuntimeError("Failed to create Index")


class FtsIndexTest(FTStest):

        COLLECTORS = {"fts_stats": True}

        @with_stats
        def index_test(self):
            logger.info('running Index Test with stats')
            self.create_index()
            start_time = time.time()
            self.wait_for_index()
            end_time = time.time()
            self.index_time_taken = end_time - start_time

        def run(self):
            super(FtsIndexTest, self).run()
            logger.info("Creating FTS index {} on {}".
                        format(self.fts_index, self.host_port))
            logger.info("Measuring the time it takes to index {} documents".
                        format(self.fts_doccount))
            self.index_test()
            self.reporter.post_to_sf(
                *self.metric_helper.calc_ftses_index(self.index_time_taken, orderbymetric=self.orderbymetric)
            )


class FTSLatencyTest(FTStest):
        COLLECTORS = {'fts_latency': True, "fts_query_stats": True, "fts_stats": True}

        def run(self):
            super(FTSLatencyTest, self).run()
            self.create_index()
            self.wait_for_index()
            self.check_rec_presist()
            self.access_bg_test()
            if self.test_config.stats_settings.enabled:
                self.reporter.post_to_sf(
                    *self.metric_helper.calc_latency_ftses_queries(percentile=80,
                                                                   dbname='fts_latency',
                                                                   metrics='cbft_latency_get',
                                                                   orderbymetric=self.orderbymetric)
                )
                self.reporter.post_to_sf(
                    *self.metric_helper.calc_latency_ftses_queries(percentile=0,
                                                                   dbname='fts_latency',
                                                                   metrics='cbft_latency_get',
                                                                   orderbymetric=self.orderbymetric)
                )


class FTSThroughputTest(FTStest):
        COLLECTORS = {'fts_query_stats': True,
                      "fts_stats": True}

        def run(self):
            super(FTSThroughputTest, self).run()
            self.create_index()
            self.wait_for_index()
            self.check_rec_presist()
            self.access_bg_test()
            if self.test_config.stats_settings.enabled:
                self.reporter.post_to_sf(
                    *self.metric_helper.calc_avg_fts_queries(orderbymetric=self.orderbymetric)
                )
