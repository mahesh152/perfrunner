import os.path
import traceback

from cbagent.collectors import Collector


class SecondaryLatencyStats(Collector):

    COLLECTOR = "secondaryscan_latency"

    def __init__(self, settings):
        super(SecondaryLatencyStats, self).__init__(settings)
        self.store.drop_db(cluster=self.cluster, collector=self.COLLECTOR)

    def _get_secondaryscan_latency(self):
        stats = {}
        if os.path.isfile(self.secondary_statsfile):
            with open(self.secondary_statsfile, 'rb') as fh:
                try:
                    next(fh).decode()
                    fh.seek(-400, 2)
                    last = fh.readlines()[-1].decode()
                    duration = last.split(',')[-1]
                    stats = {}
                    latency = duration.split(':')[1]
                    latency = latency.rstrip()
                    latency_key = duration.split(':')[0]
                    latency_key = latency_key.strip()
                    stats[latency_key] = int(latency)
                except StopIteration:
                    pass
                except Exception:
                    traceback.print_exc()
        return stats

    def sample(self):
	try:
            stats = self._get_secondaryscan_latency()
	except Exception as e:
	    traceback.print_exc()
        if stats:
            self.update_metric_metadata(stats.keys())
            self.store.append(stats, cluster=self.cluster, collector=self.COLLECTOR)

    def update_metadata(self):
        self.mc.add_cluster()
