#!/usr/bin/python
import os, sys
from glob import glob

def pipe(cmd):
    fp = os.popen(cmd)
    res = fp.read()
    stat = fp.close()
    return res, stat

def main(name, files='0*'):
    d = {}

    for file in glob(files):
        print file
        cmd = 'md5sum %s' % (file)
        res, stat = pipe(cmd)
        sig = res.split()[0]
        if sig in d:
            print 'dupe'
            cmd = 'rm %s' % file
            print cmd
            res, stat = pipe(cmd)
        d[sig] = file
        

if __name__ == '__main__':
    main(*sys.argv)
