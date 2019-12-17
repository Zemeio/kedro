# Copyright 2018-2019 QuantumBlack Visual Analytics Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND
# NONINFRINGEMENT. IN NO EVENT WILL THE LICENSOR OR OTHER CONTRIBUTORS
# BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF, OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# The QuantumBlack Visual Analytics Limited ("QuantumBlack") name and logo
# (either separately or in combination, "QuantumBlack Trademarks") are
# trademarks of QuantumBlack. The License does not grant you any right or
# license to the QuantumBlack Trademarks. You may not use the QuantumBlack
# Trademarks or any confusingly similar mark as a trademark for your product,
#     or use the QuantumBlack Trademarks in any other manner that might cause
# confusion in the marketplace, including but not limited to in advertising,
# on websites, or on software.
#
# See the License for the specific language governing permissions and
# limitations under the License.

"""``ParquetGCSDataSet`` loads and saves data to a file in gcs. It uses google-cloud-storage
to read and write from gcs and pandas to handle the Parquet file.
"""
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Dict

import gcsfs
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from kedro.contrib.io import DefaultArgumentsMixIn
from kedro.io.core import AbstractVersionedDataSet, DataSetError, Version


class ParquetGCSDataSet(DefaultArgumentsMixIn, AbstractVersionedDataSet):
    """``ParquetGCSDataSet`` loads and saves data to a file in Parquet (Google Cloud Storage).
    It uses google-cloud-storage to read and write from Parquet and pandas to handle
    the Parquet file.
    Example:
    ::

        >>> from kedro.contrib.io.gcs.parquet_gcs import ParquetGCSDataSet
        >>> import pandas as pd
        >>>
        >>> data = pd.DataFrame({'col1': [1, 2], 'col2': [4, 5],
        >>>                      'col3': [5, 6]})
        >>>
        >>> data_set = ParquetGCSDataSet(filepath="test.parquet",
        >>>                          bucket_name="test_bucket",
        >>>                          load_args=None,
        >>>                          save_args={"index": False})
        >>> data_set.save(data)
        >>> reloaded = data_set.load()
        >>>
        >>> assert data.equals(reloaded)
    """

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        filepath: str,
        bucket_name: str = None,
        credentials: Dict[str, Any] = None,
        load_args: Dict[str, Any] = None,
        save_args: Dict[str, Any] = None,
        version: Version = None,
        project: str = None,
        gcsfs_args: Dict[str, Any] = None,
    ) -> None:
        """Creates a new instance of ``ParquetGCSDataSet`` pointing to a concrete
        Parquet file on GCS.

        Args:
            filepath: Path to a Parquet file. May contain the full path in Google
                Cloud Storage including bucket and protocol, e.g.
                ``gcs://bucket-name/path/to/file.parquet``.
            bucket_name: GCS bucket name. Must be specified **only** if not
                present in ``filepath``.
            credentials: Credentials to access the GCS bucket. Authentication is performed
                by gcsfs according to https://gcsfs.readthedocs.io/en/latest/#credentials
            load_args: Pandas options for loading Parquet files.
                Here you can find all available arguments:
                https://pandas.pydata.org/pandas-docs/stable/generated/pandas.read_parquet.html
                All defaults are preserved.
            save_args: Pandas options for saving Parquet files.
                Here you can find all available arguments:
                https://pandas.pydata.org/pandas-docs/stable/generated/pandas.DataFrame.to_parquet.html
                All defaults are preserved, but "index", which is set to False.
            version: If specified, should be an instance of
                ``kedro.io.core.Version``. If its ``load`` attribute is
                None, the latest version will be loaded. If its ``save``
                attribute is None, save version will be autogenerated.
            project: The GCP project. If not specified, then the default is inferred
                by a remote request.
                https://cloud.google.com/resource-manager/docs/creating-managing-projects
            gcsfs_args: Extra arguments to pass into ``GCSFileSystem``. See
                https://gcsfs.readthedocs.io/en/latest/api.html#gcsfs.core.GCSFileSystem
        """
        _credentials = deepcopy(credentials) or {}
        _gcsfs_args = deepcopy(gcsfs_args) or {}
        _gcs = gcsfs.GCSFileSystem(project=project, token=_credentials, **_gcsfs_args)
        path = _gcs._strip_protocol(filepath)  # pylint: disable=protected-access
        path = PurePosixPath("{}/{}".format(bucket_name, path) if bucket_name else path)
        super().__init__(
            filepath=path,
            version=version,
            exists_function=_gcs.exists,
            glob_function=_gcs.glob,
            load_args=load_args,
            save_args=save_args,
        )
        self._gcs = _gcs

    def _describe(self) -> Dict[str, Any]:
        return dict(
            filepath=self._filepath,
            load_args=self._load_args,
            save_args=self._save_args,
            version=self._version,
        )

    def _load(self) -> pd.DataFrame:
        load_path = self._get_load_path()
        with self._gcs.open(str(load_path), mode="rb") as gcs_file:
            return pd.read_parquet(gcs_file, **self._load_args)

    def _save(self, data: pd.DataFrame) -> None:
        save_path = self._get_save_path()

        table = pa.Table.from_pandas(data)
        pq.write_table(
            table=table, where=save_path, filesystem=self._gcs, **self._save_args
        )

        # gcs maintain cache of the directory,
        # so invalidate to see new files
        self.invalidate_cache()

    def _exists(self) -> bool:
        try:
            load_path = self._get_load_path()
        except DataSetError:
            return False

        return self._gcs.exists(load_path)

    def invalidate_cache(self) -> None:
        """Invalidate underlying filesystem caches."""
        # gcsfs expects a string as filepath. It will crash with PosixPath.
        self._gcs.invalidate_cache(str(self._filepath))
