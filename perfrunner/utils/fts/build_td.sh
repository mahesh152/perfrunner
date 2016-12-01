# set this is couchbase bin is not in your path
#set -x
COUCHBASE_PATH="/opt/couchbase/bin/"

HIGH_HIGH=0.9
HIGH_MED=0.09
LOW_MED=0.002
LOW_LOW=0.0005

tmpfile=$(mktemp /tmp/td-$1.XXXXXX)
echo "tmpfile for collation is $tmpfile"

for f in /data/@fts/$1_*.pindex
do
  echo "Processing $f file..."
   ${COUCHBASE_PATH}cbft-bleve dump $f | grep "Dictionary Term:" | awk '{print $3,$7}' >> $tmpfile
done

sed -i 's/`//g' $tmpfile

# sort by col1
echo "sorting into $tmpfile.sorted"
sort -t ' ' -k1,1 $tmpfile > ${tmpfile}.sorted

# merge rows with same first column, sum second column
echo "merging into $tmpfile.merged"

awk -F ' ' '{
    if($1==k) {
        sum+=$2
    }
    else {
        if (NR!=1){
            printf("%s %d\n",k,sum)
            sum=$2
        }
    }
    k=$1
}' ${tmpfile}.sorted  > ${tmpfile}.merged


HH=$(bc <<<"scale=0;$HIGH_HIGH*$3")
HM=$(bc <<<"scale=0;$HIGH_MED*$3")
LM=$(bc <<<"scale=0;$LOW_MED*$3")
LL=$(bc <<<"scale=0;$LOW_LOW*$3")

# find hi,med,low terms
echo "getting high ($HM <= x < $HH) - ${tmpfile}.hi"
awk -v hh="$HH" -v hm="$HM" -F ' ' '{
  if(($2 < hh) && ($2 >= hm)) {
    printf("%s %d\n",$1,$2)
  }
}' ${tmpfile}.merged > ${tmpfile}.hi

echo "getting med ($LM <= x < $HM) - ${tmpfile}.med"
awk -v hm="$HM" -v lm="$LM" -F ' ' '{
  if(($2 < hm) && ($2 >= lm)) {
    printf("%s %d\n",$1,$2)
  }
}' ${tmpfile}.merged > ${tmpfile}.med

echo "getting low ($LL <= x < $LM) - ${tmpfile}.low"
awk -v lm="$LM" -v ll="$LL" -F ' ' '{
  if(($2 < lm) && ($2 >= ll)) {
    printf("%s %d\n",$1,$2)
  }
}' ${tmpfile}.merged > ${tmpfile}.low

# check too high
awk -v hh="$HH" -F ' ' 'BEGIN{
  count = 0
}{
  if($2 >= hh) {
    count++
  }
}
END{
  printf("too high: %d\n", count)
}' ${tmpfile}.merged

# check too low
awk -v ll="$LL" -F ' ' 'BEGIN{
  count = 0
}{
  if($2 < ll) {
    count++
  }
}
END{
  printf("too low: %d\n", count)
}' ${tmpfile}.merged