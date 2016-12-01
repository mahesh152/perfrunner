import os.path

from cbagent.collectors import Collector


class SecondaryLatencyStats(Collector):

    COLLECTOR = "secondaryscan_latency"

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
        return stats

    def sample(self):
        stats = self._get_secondaryscan_latency()
        if stats:
            self.update_metric_metadata(stats.keys())
            self.store.append(stats, cluster=self.cluster, collector=self.COLLECTOR)

    def update_metadata(self):
        self.mc.add_cluster()
