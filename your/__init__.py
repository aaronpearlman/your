#!/usr/bin/env python3
import logging
logger = logging.getLogger(__name__)
from your.psrfits import PsrfitsFile
from your.pysigproc import SigprocFile

class Your(PsrfitsFile, SigprocFile):
    def __init__(self, file):
        if isinstance(file, str):
            ext = os.path.splitext(file)[1]
            if ext == ".fits" or ext == ".sf":
                PsrfitsFile.__init__(self, psrfitslist = [file])
            elif ext == ".fil":
                SigprocFile.__init__(self, fp=file)
            else:
                raise TypeError('Filetype not supported')
        elif isinstance(file, list):
            for f in file:                
                try:
                    assert os.path.splitext(f)[1] == '.fits'
                except AssertionError:
                    logger.exception("Filelist contains files other than .fits")
            file.sort()
            PsrfitsFile.__init__(self, psrfitslist=file)
        else:
            raise TypeError('Input file is neither a string not a list of string')
