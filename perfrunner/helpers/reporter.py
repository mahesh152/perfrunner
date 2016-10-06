import os
import time
from datetime import datetime
from zipfile import ZIP_DEFLATED, ZipFile

import requests
from couchbase import Couchbase
from couchbase.bucket import Bucket
from logger import logger
from pytz import timezone

from perfrunner.helpers.misc import pretty_dict, uhex


class Comparator(object):

    CONFIDENCE_THRESHOLD = 50

    def get_snapshots(self, benckmark):
        """Get all snapshots ordered by build version for given benchmark"""
        self.snapshots_by_build = dict()
        for row in self.cbb.query('benchmarks', 'build_and_snapshots_by_metric',
                                  key=benckmark['metric'], stale='false'):
            self.snapshots_by_build[row.value[0]] = row.value[1]

    def find_previous(self, new_build):
        """Find previous build within current release or latest build from
        previous release"""
        all_builds = sorted(self.snapshots_by_build.keys(), reverse=True)
        try:
            return all_builds[all_builds.index(new_build) + 1:][0]
        except IndexError:
            return
        except ValueError:
            logger.warn('Didn\'t find {} in {}'.format(new_build, all_builds))

    def _compare(self, cbmonitor, prev_build, new_build):
        """Compare snapshots if possible"""
        api = 'http://{}/reports/compare/'.format(cbmonitor['host'])
        snapshot_api = 'http://{}/reports/html/?snapshot={{}}&snapshot={{}}'\
            .format(cbmonitor['host'])

        changes = []
        reports = []
        if prev_build is not None:
            baselines = self.snapshots_by_build[prev_build]
            targets = self.snapshots_by_build[new_build]
            if baselines and targets:
                for baseline, target in zip(baselines, targets):
                    params = {'baseline': baseline, 'target': target}
                    comparison = requests.get(url=api, params=params).json()
                    diff = tuple({
                        m for m, confidence in comparison
                        if confidence > self.CONFIDENCE_THRESHOLD
                    })
                    if diff:
                        changes.append((prev_build, diff))
                        snapshots_url = snapshot_api.format(baseline, target)
                        reports.append((prev_build, snapshots_url))

            # Prefetch (trigger) HTML reports
            for _, url in reports:
                requests.get(url=url)

        return {'changes': changes, 'reports': reports}

    def __call__(self, test, benckmark):
        showfast = test.test_config.stats_settings.showfast
        cbmonitor = test.test_config.stats_settings.cbmonitor
        try:
            self.cbb = Couchbase.connect(bucket='benchmarks', **showfast)
            self.cbf = Couchbase.connect(bucket='feed', **showfast)
        except Exception as e:
            logger.warn('Failed to connect to database, {}'.format(e))
            return

        self.get_snapshots(benckmark)
        prev_build = self.find_previous(new_build=benckmark['build'])

        # Feed record
        _id = str(int(time.time() * 10 ** 6))
        base_feed = {
            'build': benckmark['build'],
            'cluster': test.cluster_spec.name,
            'test_config': test.test_config.name,
            'summary': test.test_config.test_case.test_summary,
            'datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        changes = self._compare(cbmonitor=cbmonitor,
                                prev_build=prev_build,
                                new_build=benckmark['build'])
        feed = dict(base_feed, **changes)
        self.cbf.set(_id, feed)
        logger.info('Snapshot comparison: {}'.format(pretty_dict(feed)))


class SFReporter(object):

    def __init__(self, test):
        self.test = test

    def _add_cluster(self):
        cluster = self.test.cluster_spec.name
        params = self.test.cluster_spec.parameters
        showfast = self.test.test_config.stats_settings.showfast
        try:
            cb = Couchbase.connect(bucket='clusters', **showfast)
            cb.set(cluster, params)
        except Exception as e:
            logger.warn('Failed to add cluster, {}'.format(e))
        else:
            logger.info('Successfully posted: {}, {}'.format(
                cluster, pretty_dict(params)
            ))

    def _add_metric(self, metric, metric_info):
        if metric_info is None:
            metric_info = {
                'title': self.test.test_config.test_case.metric_title,
                'cluster': self.test.cluster_spec.name,
                'larger_is_better': self.test.test_config.test_case.larger_is_better,
            }
        showfast = self.test.test_config.stats_settings.showfast
        try:
            cb = Couchbase.connect(bucket='metrics', **showfast)
            cb.set(metric, metric_info)
        except Exception as e:
            logger.warn('Failed to add cluster, {}'.format(e))
        else:
            logger.info('Successfully posted: {}, {}'.format(
                metric, pretty_dict(metric_info)
            ))

    def _prepare_data(self, metric, value):
        key = uhex()
        data = {
            'build': self.test.build,
            'metric': metric,
            'value': value,
            'snapshots': self.test.snapshots,
            'build_url': os.environ.get('BUILD_URL')
        }
        if self.test.master_events:
            data.update({'master_events': key})
        return key, data

    @staticmethod
    def _mark_previous_as_obsolete(cb, benckmark):
        for row in cb.query('benchmarks', 'values_by_build_and_metric',
                            key=[benckmark['metric'], benckmark['build']]):
            doc = cb.get(row.docid)
            doc.value.update({'obsolete': True})
            cb.set(row.docid, doc.value)

    def _log_benchmark(self, metric, value):
        key, benckmark = self._prepare_data(metric, value)
        logger.info('Dry run stats: {}'.format(
            pretty_dict(benckmark)
        ))
        return key

    def _post_benckmark(self, metric, value):
        key, benckmark = self._prepare_data(metric, value)
        showfast = self.test.test_config.stats_settings.showfast

        cb = Couchbase.connect(bucket='benchmarks', **showfast)
        try:
            self._mark_previous_as_obsolete(cb, benckmark)
            cb.set(key, benckmark)
        except Exception as e:
            logger.warn('Failed to post benchmark {}: {}'.format(e, benckmark))
        else:
            logger.info('Successfully posted: {}'.format(pretty_dict(benckmark)))

        return key

    def _upload_master_events(self, filename):
        api = 'http://{}/cbmonitor/add_master_events/'.format(
            self.test.test_config.stats_settings.cbmonitor['host'])
        data = {
            'filename': filename,
            'master_events': self.test.master_events[0],
        }
        requests.post(url=api, data=data)

    def post_to_sf(self, value, metric=None, metric_info=None):
        if metric is None:
            metric = '{}_{}'.format(self.test.test_config.name,
                                    self.test.cluster_spec.name)
        stats_settings = self.test.test_config.stats_settings
        if stats_settings.post_to_sf:
            self._add_metric(metric, metric_info)
            self._add_cluster()
            key = self._post_benckmark(metric, value)
        else:
            key = self._log_benchmark(metric, value)
        if key and self.test.master_events:
            self._upload_master_events(filename=key)
        return value

    def _upload_test_run_dailyp(self, test_run_dict):
        try:
            bucket = Bucket('couchbase://{}/perf_daily'.
                            format(self.test.test_config.stats_settings.cbmonitor['host']))
        except Exception as e:
            logger.info("Post to Dailyp, DB connection error: {}".format(e.message))
            return False
        docid = "{}__{}__{}__{}__{}".format(test_run_dict['category'],
                                            test_run_dict['subcategory'],
                                            test_run_dict['test'],
                                            test_run_dict['build'],
                                            test_run_dict['datetime'])
        bucket.upsert(docid, test_run_dict)
        return True

    def post_to_dailyp(self, metrics):
        test_title = self.test.test_config.test_case.metric_title
        test_name = test_title.replace(', ', '_')
        replace_chars = ", =/.`\\"
        for c in replace_chars:
            test_name = test_name.replace(c, "_")

        snapshot_links = list()
        snapshot_host = "http://{}/reports/html/?snapshot=".\
                        format(self.test.test_config.stats_settings.cbmonitor['host'])
        for snapshot in self.test.cbagent.snapshots:
            snapshot_link = snapshot_host + snapshot
            snapshot_links.append(snapshot_link)

        if self.test.test_config.dailyp_settings.subcategory is not None:
            category_full_name = "{}-{}".format(self.test.test_config.dailyp_settings.category,
                                                self.test.test_config.dailyp_settings.subcategory)
        else:
            category_full_name = self.test.test_config.dailyp_settings.category

        post_body = {"category": category_full_name,
                     "subcategory": self.test.test_config.dailyp_settings.subcategory,
                     "test_title": test_title,
                     "datetime": datetime.now(timezone('US/Pacific')).strftime("%Y_%m_%d-%H:%M"),
                     "build": self.test.build,
                     "test": test_name,
                     "metrics": metrics,
                     "snapshots": snapshot_links
                     }
        if self._upload_test_run_dailyp(post_body):
            logger.info("Successfully posted to Dailyp {}".format(post_body))
        else:
            logger.warn("Failed to post to Dailyp {}".format(post_body))


class LogReporter(object):

    def __init__(self, test):
        self.test = test

    def save_master_events(self):
        with ZipFile('master_events.zip', 'w', ZIP_DEFLATED) as zh:
            for master in self.test.cluster_spec.yield_masters():
                master_events = self.test.rest.get_master_events(master)
                self.test.master_events.append(master_events)
                fname = 'master_events_{}.log'.format(master.split(':')[0])
                zh.writestr(zinfo_or_arcname=fname, bytes=master_events)


class Reporter(SFReporter, LogReporter):

    def start(self):
        self.ts = time.time()

    def finish(self, action, time_elapsed=None):
        time_elapsed = time_elapsed or (time.time() - self.ts)
        time_elapsed = round(time_elapsed / 60, 2)
        logger.info(
            'Time taken to perform "{}": {} min'.format(action, time_elapsed)
        )
        return time_elapsed
