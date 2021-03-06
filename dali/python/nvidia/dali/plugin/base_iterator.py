# Copyright (c) 2020, NVIDIA CORPORATION. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from nvidia.dali import types
import math
import logging
import numpy as np
import warnings

def _iterator_deprecation_warning():
    warnings.warn("Please set `reader_name` and don't set last_batch_padded and size manually " +
                  " whenever possible. This may lead, in some situations, to miss some " +
                  " samples or return duplicated ones. Check the Sharding section of the "
                  "documentation for more details.",
                  Warning, stacklevel=2)

class _DaliBaseIterator(object):
    """
    DALI base iterator class. Shouldn't be used directly.

    Parameters
    ----------
    pipelines : list of nvidia.dali.pipeline.Pipeline
                List of pipelines to use
    output_map : list of (str, str)
                 List of pairs (output_name, tag) which maps consecutive
                 outputs of DALI pipelines to proper field in MXNet's
                 DataBatch.
                 tag is one of DALIGenericIterator.DATA_TAG
                 and DALIGenericIterator.LABEL_TAG mapping given output
                 for data or label correspondingly.
                 output_names should be distinct.
    size : int, default = -1
                Number of samples in the shard for the wrapped pipeline (if there is more than one it is a sum)
                Providing -1 means that the iterator will work until StopIteration is raised
                from the inside of iter_setup(). The options `fill_last_batch`, `last_batch_padded` and
                `auto_reset` don't work in such case. It works with only one pipeline inside
                the iterator.
                Mutually exclusive with `reader_name` argument
    reader_name : str, default = None
                Name of the reader which will be queried to the shard size, number of shards and
                all other properties necessary to count properly the number of relevant and padded
                samples that iterator needs to deal with. It automatically sets `fill_last_batch` and
                `last_batch_padded` accordingly to match the reader's configuration
    auto_reset : bool, optional, default = False
                Whether the iterator resets itself for the next epoch
                or it requires reset() to be called separately.
    fill_last_batch : bool, optional, default = True
                Whether to fill the last batch with data up to 'self.batch_size'.
                The iterator would return the first integer multiple
                of self._num_gpus * self.batch_size entries which exceeds 'size'.
                Setting this flag to False will cause the iterator to return
                exactly 'size' entries.
    last_batch_padded : bool, optional, default = False
                Whether the last batch provided by DALI is padded with the last sample
                or it just wraps up. In the conjunction with ``fill_last_batch`` it tells
                if the iterator returning last batch with data only partially filled with
                data from the current epoch is dropping padding samples or samples from
                the next epoch (it doesn't literally drop but sets ``pad`` field of ndarray
                so the following code could use it to drop the data). If set to False next
                epoch will end sooner as data from it was consumed but dropped. If set to
                True next epoch would be the same length as the first one. For this to happen,
                the option `pad_last_batch` in the reader needs to be set to True as well.
                It is overwritten when `reader_name` argument is provided

    Example
    -------
    With the data set ``[1,2,3,4,5,6,7]`` and the batch size 2:

    fill_last_batch = False, last_batch_padded = True  -> last batch = ``[7]``, next iteration will return ``[1, 2]``

    fill_last_batch = False, last_batch_padded = False -> last batch = ``[7]``, next iteration will return ``[2, 3]``

    fill_last_batch = True, last_batch_padded = True   -> last batch = ``[7, 7]``, next iteration will return ``[1, 2]``

    fill_last_batch = True, last_batch_padded = False  -> last batch = ``[7, 1]``, next iteration will return ``[2, 3]``
    """
    def __init__(self,
                 pipelines,
                 size=-1,
                 reader_name=None,
                 auto_reset=False,
                 fill_last_batch=True,
                 last_batch_padded=False):

        assert pipelines is not None, "Number of provided pipelines has to be at least 1"
        if not isinstance(pipelines, list):
            pipelines = [pipelines]
        self._num_gpus = len(pipelines)
        # frameworks expect from its data iterators to have batch_size field,
        # so it is not possible to use _batch_size instead
        self.batch_size = pipelines[0].batch_size
        assert np.all(np.equal([pipe.batch_size for pipe in pipelines], self.batch_size)), \
                "All pipelines should have the same batch size set"

        self._size = int(size)
        self._auto_reset = auto_reset

        self._fill_last_batch = fill_last_batch
        self._last_batch_padded = last_batch_padded
        assert self._size != 0, "Size cannot be 0"
        assert self._size > 0 or (self._size < 0 and (len(pipelines) == 1 or reader_name)), "Negative size is supported only for a single pipeline"
        assert not reader_name or (reader_name and self._size < 0), "When reader_name is provided, size should not be set"
        assert not reader_name or (reader_name and last_batch_padded == False), "When reader_name is provided, last_batch_padded should not be set"
        if self._size < 0 and not reader_name:
            self._auto_reset = False
            self._fill_last_batch = False
            self._last_batch_padded = False
        if self.size > 0 and not reader_name:
            _iterator_deprecation_warning()
        self._pipes = pipelines
        self._counter = 0

        # Build all pipelines
        for p in self._pipes:
            with p._check_api_type_scope(types.PipelineAPIType.ITERATOR):
                p.build()

        self._reader_name = reader_name
        self._extract_from_reader_and_validate()

    def _calculate_shard_sizes(self, shard_nums):
        shards_beg = np.floor(shard_nums * self._size_no_pad / self._shards_num).astype(np.int)
        shards_end = np.floor((shard_nums + 1) * self._size_no_pad / self._shards_num).astype(np.int)
        return shards_end - shards_beg

    def _extract_from_reader_and_validate(self):
        if self._reader_name:
            readers_meta = [p.reader_meta(self._reader_name) for p in self._pipes]

            def err_msg_gen(err_msg):
                'Reader Operator should have the same {} in all the pipelines.'.format(err_msg)

            def check_equality_and_get(input_meta, name, err_msg):
                assert np.all(np.equal([meta[name] for meta in input_meta], input_meta[0][name])), \
                err_msg_gen(err_msg)
                return input_meta[0][name]

            def check_all_or_none_and_get(input_meta, name, err_msg):
                assert np.all([meta[name] for meta in readers_meta]) or \
                   not np.any([meta[name] for meta in readers_meta]), \
                err_msg_gen(err_msg)
                return input_meta[0][name]

            self._size_no_pad = check_equality_and_get(readers_meta, "epoch_size", "size value")
            self._shards_num = check_equality_and_get(readers_meta, "number_of_shards", "`num_shards` argument set")
            self._last_batch_padded = check_all_or_none_and_get(readers_meta, "pad_last_batch", "`pad_last_batch` argument set")
            self._is_stick_to_shard = check_all_or_none_and_get(readers_meta, "stick_to_shard", "`stick_to_shard` argument set")

            self._shards_id = np.array([meta["shard_id"] for meta in readers_meta], dtype=np.int)

            if self._last_batch_padded:
                # if padding is enabled all shards are equal
                self._size = readers_meta[0]["epoch_size_padded"] // self._shards_num
            else:
                # get the size as a multiply of the batch size that is bigger or equal than the biggest shard
                self._size = math.ceil(math.ceil(self._size_no_pad / self._shards_num) / self.batch_size) * self.batch_size

            # count where we starts inside each GPU shard in given epoch,
            # if shards are uneven this will differ epoch2epoch
            self._counter_per_gpu = np.zeros(self._shards_num, dtype=np.long)
            self._shard_sizes_per_gpu = self._calculate_shard_sizes(np.arange(0, self._shards_num))

            # to avoid recalculation of shard sizes when iterator moves across the shards
            # memorize the initial shard sizes and then use chaning self._shards_id to index it
            self._shard_sizes_per_gpu_initial = self._shard_sizes_per_gpu.copy()

    def _check_stop(self):
        """"
        Checks iterator stop condition and raise StopIteration if needed
        """
        if self._counter >= self._size and self._size > 0:
            if self._auto_reset:
                self.reset()
            raise StopIteration

    def _remove_padded(self):
        """
        Checks if remove any padded sample and how much.

        Calculates the number of padded samples in the batch for each pipeline
        wrapped up by the iterator. Returns if there is any padded data that
        needs to be dropped and if so how many samples in each GPU
        """
        if_drop = False
        left = -1
        if not self._fill_last_batch:
            # calculate each shard size for each id, and check how many samples are left by substracting
            # from iterator counter the shard size, then go though all GPUs and check how much data needs to be dropped
            left = self.batch_size - (self._counter - self._shard_sizes_per_gpu_initial[self._shards_id])
            if_drop = np.less(left, self.batch_size)
        return if_drop, left

    def reset(self):
        """
        Resets the iterator after the full epoch.
        DALI iterators do not support resetting before the end of the epoch
        and will ignore such request.
        """
        if self._counter >= self._size or self._size < 0:
            if self._fill_last_batch and not self._last_batch_padded:
                if self._reader_name:
                    # accurate way
                    # get the number of samples read in this epoch by each GPU
                    # self._counter had initial value of min(self._counter_per_gpu) so substract this to get the actual value
                    self._counter -= min(self._counter_per_gpu)
                    self._counter_per_gpu = self._counter_per_gpu + self._counter
                    # check how much each GPU read ahead from next shard, as shards have different size each epoch
                    # GPU may read ahead or not
                    self._counter_per_gpu = self._counter_per_gpu - self._shard_sizes_per_gpu
                    # to make sure that in the next epoch we read the whole shard we need to set start value to the smallest one
                    self._counter = min(self._counter_per_gpu)
                else:
                    # legacy way
                    self._counter = self._counter % self._size
            else:
                self._counter = 0
            # advance to the next shard
            if self._reader_name:
                if not self._is_stick_to_shard:
                    # move shards id for wrapped pipeliens
                    self._shards_id = (self._shards_id + 1) % self._shards_num
                # revaluate _size
                if self._fill_last_batch and not self._last_batch_padded:
                    # move all shards ids GPU ahead
                    if not self._is_stick_to_shard:
                        self._shard_sizes_per_gpu = np.roll(self._shard_sizes_per_gpu, 1)
                    # check how many samples we need to reach from each shard in next epoch per each GPU
                    # taking into account already read
                    read_in_next_epoch = self._shard_sizes_per_gpu - self._counter_per_gpu
                    # get the maximmum number of samples and round it up to full batch sizes
                    self._size = math.ceil(max(read_in_next_epoch) / self.batch_size) * self.batch_size
                    # in case some epoch is skipped because we have read ahead in this epoch so much
                    # that in the next one we done already
                    if self._size == 0:
                        # it means that self._shard_sizes_per_gpu == self._counter_per_gpu, so we can
                        # jump to the next epoch and zero self._counter_per_gpu
                        self._counter_per_gpu = np.zeros(self._shards_num, dtype=np.long)
                        # self._counter = min(self._counter_per_gpu), but just set 0 to make it simpler
                        self._counter = 0
                        # roll once again
                        self._shard_sizes_per_gpu = np.roll(self._shard_sizes_per_gpu, 1)
                        # as self._counter_per_gpu is 0 we can just use
                        # read_in_next_epoch = self._shard_sizes_per_gpu
                        self._size = math.ceil(max(self._shard_sizes_per_gpu) / self.batch_size) * self.batch_size

            for p in self._pipes:
                p.reset()
                if p.empty():
                    with p._check_api_type_scope(types.PipelineAPIType.ITERATOR):
                        p.schedule_run()
        else:
            logging.warning("DALI iterator does not support resetting while epoch is not finished. Ignoring...")

    def next(self):
        """
        Returns the next batch of data.
        """
        return self.__next__()

    def __next__(self):
        raise NotImplementedError

    def __iter__(self):
        return self

    @property
    def size(self):
        return self._size