[clusters]
secondary =
    10.142.150.101:8091
    10.142.150.102:8091,index
    10.142.150.103:8091
    10.142.150.104:8091

[clients]
hosts =
    10.142.150.105
credentials = root:couchbase

[storage]
data = /data
index = /data

[credentials]
rest = Administrator:couchbase
ssh = root:couchbase

[parameters]
Platform = HW
OS = Ubuntu 14
CPU = Data: i7-4870HQ (48 vCPU), Index: i7-4870HQ (48 vCPU)
Memory = Data: 1 GB, Index: 1 GB
Disk = RAID 1 SSD