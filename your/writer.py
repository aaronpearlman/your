#!/usr/bin/env python3

import logging
import os

import numpy as np
import tqdm
from astropy.io import fits

from your.formats.filwriter import write_fil
from your.formats.fitswriter import initialize_psrfits
from your.utils.rfi import sk_sg_filter

logger = logging.getLogger(__name__)


class Writer:
    """
    Writer class

    Args:

        y: Your object

        nstart: Start sample to read from

        nsamp: Number of samples to write

        c_min: Starting channel index (default: 0)

        c_max: End channel index (default: total number of frequencies)

        outdir: Output directory for file

        outname: Name of the file to write to (without the file extension)

        progress: Turn on/off progress bar

        flag_rfi: To turn on RFI flagging

        sk_sig: Sigma for spectral kurtosis filter

        sg_fw: Filter window for savgol filter

        sg_sig: Sigma for savgol filter

        zero_dm_subt: Enable zero-DM RFI excision

    """

    def __init__(self, y, nstart=None, nsamp=None, c_min=None, c_max=None, outdir=None, outname=None, flag_rfi=False,
               progress=None, sk_sig=4, sg_fw=15, sg_sig=4, zero_dm_subt=False):

        self.your_obj = y
        self.nstart = nstart
        self.nsamp = nsamp

        if not self.nstart:
            self.nstart = 0

        if c_min:
            self.c_min = c_min
        else:
            self.c_min = 0

        if c_max:
            self.c_max = c_max
        else:
            self.c_max = len(self.your_obj.chan_freqs)

        if self.c_max < self.c_min:
            logging.warning('Start channel index is larger than end channel index. Swapping them.')
            self.c_min, self.c_max = self.c_max, self.c_min

        self.outdir = outdir
        self.outname = outname
        self.flag_rfi = flag_rfi
        self.progress = progress
        self.sk_sig = sk_sig
        self.sg_fw = sg_fw
        self.sg_sig = sg_sig
        self.zero_dm_subt = zero_dm_subt
        self.chan_freqs = None
        self.nchans = None
        self.data = None
        self.set_freqs()

        original_dir, orig_basename = os.path.split(self.your_obj.your_header.filename)
        if not self.outname:
            name, ext = os.path.splitext(orig_basename)
            if ext == '.fits':
                temp = name.split('_')
                if len(temp) > 1:
                    self.outname = '_'.join(temp[:-1]) + '_converted'
                else:
                    self.outname = name + '_converted'
            else:
                self.outname = name + '_converted'

        if not self.outdir:
            self.outdir = original_dir

    def set_freqs(self):
        """
        Sets chan_freqs and nchans based on input channel selection

        """
        self.chan_freqs = self.your_obj.chan_freqs[self.c_min:self.c_max]
        self.nchans = len(self.chan_freqs)

    def get_data_to_write(self, st, samp):
        """
        Read data to self.data, selects channels
        Optionally perform RFI filtering and zero-DM subtraction

        Args:

            st: Start sample number to read from

            samp: Number of samples to read

        """
        data = self.your_obj.get_data(st, samp)
        data = data[:, self.c_min:self.c_max]
        if self.flag_rfi:
            mask = sk_sg_filter(data, self.your_obj, self.sk_sig, self.nchans, self.sg_fw, self.sg_sig)

            if self.your_obj.your_header.dtype == np.uint8:
                data[:, mask] = np.around(np.mean(data[:, ~mask]))
            else:
                data[:, mask] = np.mean(data[:, ~mask])

        if self.zero_dm_subt:
            logger.debug('Subtracting 0-DM time series from the data')
            data = data - data.mean(1)[:, None]

        data = data.astype(self.your_obj.your_header.dtype)
        self.data = data

    def to_fil(self):
        """
        Writes out a Filterbank File.

        """

        # Calculate loop of spectra
        if not self.nsamp:
            self.nsamp = self.your_obj.your_header.native_nspectra

        interval = 4096 * 24
        if self.nsamp < interval:
            interval = self.nsamp

        if self.nsamp > interval:
            nloops = 1 + self.nsamp // interval
        else:
            nloops = 1
        nstarts = np.arange(self.nstart, interval * nloops, interval, dtype=int)
        nsamps = np.full(nloops, interval)
        if nsamps % interval != 0:
            nsamps = np.append(nsamps, [nsamps % interval])

        # Read data
        for st, samp in tqdm.tqdm(zip(nstarts, nsamps), total=len(nstarts), disable=self.progress):
            logger.debug(f'Reading spectra {st}-{st + samp} in file {self.your_obj.your_header.filename}')
            self.get_data_to_write(st, samp)
            logger.info(
                f'Writing data from spectra {st}-{st + samp} in the frequency channel range {self.c_min}-{self.c_max} '
                f'to filterbank')
            write_fil(self.data, self.your_obj, nchans=self.nchans, chan_freq=self.chan_freqs, outdir=self.outdir,
                      filename=self.outname + '.fil', nstart=self.nstart)
            logger.debug(f'Successfully written data from spectra {st}-{st + samp} to filterbank')

        logging.debug(f'Read all the necessary spectra')

    def to_fits(self, npsub=-1):
        """
        Writes out a PSRFITS file

        Args:

            npsub: number of spectra per subint
        
        """

        tsamp = self.your_obj.your_header.tsamp

        if npsub == -1:
            npsub = int(1.0 / tsamp)
        else:
            pass

        if self.nsamp:
            if self.nsamp < npsub:
                npsub = self.nsamp

        outfile = self.outdir + '/' + self.outname + '.fits'

        initialize_psrfits(outfile=outfile, y=self.your_obj, npsub=npsub, nstart=self.nstart, nsamp=self.nsamp,
                           chan_freqs=self.chan_freqs)

        nifs = self.your_obj.your_header.npol

        logger.info("Filling PSRFITS file with data")

        # Open PSRFITS file
        hdulist = fits.open(outfile, mode='update')
        hdu = hdulist[1]
        nsubints = len(hdu.data[:]['data'])

        # Loop through chunks of data to write to PSRFITS
        n_read_subints = 10
        logger.info(f'Number of subints to write {nsubints}')

        st = self.nstart
        for istart in tqdm.tqdm(np.arange(0, nsubints, n_read_subints), disable=self.progress):
            istop = istart + n_read_subints
            if istop > nsubints:
                istop = nsubints
            else:
                pass
            isub = istop - istart

            logger.info(f"Writing data to {outfile} from subint = {istart} to {istop}.")

            # Read in nread samples from filfile
            nread = isub * npsub
            self.get_data_to_write(st, nread)
            data = self.data
            st += nread

            nvals = isub * npsub * nifs
            if data.shape[0] < nvals:
                logger.debug(f'nspectra in this chunk ({data.shape[0]}) < nsubints * npsub * nifs ({nvals})')
                logger.debug(f'Appending zeros at the end to fill the subint')
                pad_back = np.zeros((nvals - data.shape[0], data.shape[1]))
                data = np.vstack((data, pad_back))
            else:
                pass

            data = np.reshape(data, (isub, npsub, nifs, self.nchans))

            # If foff is negative, we need to flip the freq axis
#            if foff < 0:
#                logger.debug(f"Flipping band as {foff} < 0")
#                data = data[:, :, :, ::-1]
#            else:
#                pass

            # Put data in hdu data array
            logger.debug(f'Writing data of shape {data.shape} to {outfile}.')
            hdu.data[istart:istop]['data'][:, :, :, :] = data[:].astype(self.your_obj.your_header.dtype)

            # Write to file
            hdulist.flush()

        logger.info(f'All spectra written to {outfile}')
        # Close open FITS file
        hdulist.close()
