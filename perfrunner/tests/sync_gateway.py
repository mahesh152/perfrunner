import json
import time
from collections import defaultdict

import numpy as np
from collections import OrderedDict
from jinja2 import Environment, FileSystemLoader
from logger import logger
from seriesly import Seriesly

from perfrunner.helpers.cbmonitor import with_stats
from perfrunner.helpers.metrics import SgwMetricHelper, MetricHelper
from perfrunner.helpers.misc import log_phase, pretty_dict
from perfrunner.helpers.rest import SyncGatewayRequestHelper
from perfrunner.settings import SGW_SERIESLY_HOST
from perfrunner.tests import PerfTest


class GateloadTest(PerfTest):

    KPI = 'PushToSubscriberInteractive/p{} average'

    def __init__(self, *args, **kwargs):
        super(GateloadTest, self).__init__(*args, **kwargs)
        self.metric_helper = SgwMetricHelper(self)
        self.metric_db_servers_helper = MetricHelper(self)
        self.request_helper = SyncGatewayRequestHelper()

        loader = FileSystemLoader('templates')
        self.env = Environment(loader=loader)

    def create_sgw_test_config(self):
        logger.info('Creating bash configuration')

        template = self.env.get_template('sgw_test_config.sh')
        with open('scripts/sgw_test_config.sh', 'w') as fh:
            fh.write(template.render(
                gateways_ip=' '.join(self.remote.gateways),
                gateloads_ip=' '.join(self.remote.gateloads),
                dbs_ip=' '.join(self.cluster_spec.yield_hostnames()),
                seriesly_ip=SGW_SERIESLY_HOST,
            ))

    def start_test_info(self):
        self.create_sgw_test_config()
        self.remote.start_test_info()

    def start_samplers(self):
        logger.info('Creating seriesly dbs')
        seriesly = Seriesly(host='{}'.format(SGW_SERIESLY_HOST))
        for i, _ in enumerate(self.remote.gateways, start=1):
            seriesly.create_db('gateway_{}'.format(i))
            seriesly.create_db('gateload_{}'.format(i))
        self.remote.start_sampling()

    def generate_gateload_configs(self):
        template = self.env.get_template('gateload_config_template.json')

        for idx, gateway in enumerate(self.remote.gateways):
            config_fname = 'templates/gateload_config_{}.json'.format(idx)
            with open(config_fname, 'w') as fh:
                fh.write(template.render(
                    gateway=gateway,
                    pushers=self.test_config.gateload_settings.pushers,
                    pullers=self.test_config.gateload_settings.pullers,
                    doc_size=self.test_config.gateload_settings.doc_size,
                    sleep_time=self.test_config.gateload_settings.sleep_time,
                    run_time=self.test_config.gateload_settings.run_time * 1000,
                    idx=idx,
                ))

    def collect_kpi(self):
        logger.info('Collecting Sync Gateway KPI')

        criteria = OrderedDict((
            (95, self.test_config.gateload_settings.p95_avg_criteria),
            (99, self.test_config.gateload_settings.p99_avg_criteria),
        ))

        summary = defaultdict(dict)
        latencies = defaultdict(list)
        all_requests_per_sec = []
        for idx, gateload in enumerate(self.remote.gateloads, start=1):
            requests_per_sec = self.metric_helper.calc_sync_gateway_requests_per_sec(idx=idx)
            all_requests_per_sec.append(requests_per_sec)
            for p in criteria:
                kpi = self.KPI.format(p)
                latency = self.metric_helper.calc_push_latency(p=p, idx=idx)
                summary[gateload][kpi] = latency
                summary[gateload]['requests per sec'] = requests_per_sec
                latencies[p].append(latency)
        logger.info('Per node summary: {}'.format(pretty_dict(summary)))

        self.pass_fail = []
        for p, criterion in criteria.items():
            kpi = self.KPI.format(p)
            average = np.mean(latencies[p])
            if average > criterion:
                status = '{}: {} - doesn\'t meet the criteria of {}'\
                    .format(kpi, average, criterion)
            else:
                status = '{}: {} - meets the criteria of {}'\
                    .format(kpi, average, criterion)
            self.pass_fail.append(status)
        logger.info(
            'Aggregated latency: {}'.format(pretty_dict(self.pass_fail))
        )

        network_matrix = self.metric_db_servers_helper.calc_network_throughput
        network_matrix['Avg requests  per sec'] = int(np.average(all_requests_per_sec))
        logger.info(
            'Network throughput: {}'.format(json.dumps(network_matrix, indent=4))
        )

    @with_stats
    def workload(self):
        logger.info('Sleep {} seconds waiting for test to finish'.format(
            self.test_config.gateload_settings.run_time
        ))
        time.sleep(self.test_config.gateload_settings.run_time)

    def run(self):
        self.start_test_info()

        self.generate_gateload_configs()
        self.remote.start_gateload()
        for idx, gateload in enumerate(self.remote.gateloads, start=1):
            self.request_helper.wait_for_gateload_to_start(idx, gateload)

        self.remote.restart_seriesly()
        self.request_helper.wait_for_seriesly_to_start(SGW_SERIESLY_HOST)
        self.start_samplers()

        log_phase('Gateload settings', self.test_config.gateload_settings)
        log_phase('Gateway settings', self.test_config.gateway_settings)

        self.workload()

        return self.collect_kpi()


class PassFailGateloadTest(GateloadTest):

    def run(self):
        super(PassFailGateloadTest, self).run()
        if 'doesn\'t meet' in ''.join(self.pass_fail):
            logger.interrupt('Test failed')
