[clusters]
ycsb3 =
    172.23.100.190:8091
    172.23.100.191:8091
    172.23.100.192:8091


[clients]
hosts =
    172.23.100.194
credentials = root:couchbase


[storage]
data = /data
index = /data


[credentials]
rest = Administrator:password
ssh = root:couchbase


[parameters]
Platform = HW
OS = CentOS 7
CPU = E5-2680 v3 (48 vCPU)
Memory = 64GB
Disk = SSD
