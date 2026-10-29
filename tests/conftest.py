"""Shared pytest fixtures: synthetic DICOM builders.

Everything here is synthetic - no patient data, no network beyond localhost.
A throwaway Storage SCP helper is added in the receive/export phase.
"""

from __future__ import annotations

import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian, CTImageStorage


def _item(**kw) -> Dataset:
    d = Dataset()
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def make_ct(sop_instance=None, **over) -> Dataset:
    """A minimal valid single-frame CT image dataset (uncompressed)."""
    ds = Dataset()
    ds.PatientName = over.get("PatientName", "DOE^JANE")
    ds.PatientID = over.get("PatientID", "MRN1")
    ds.PatientBirthDate = over.get("PatientBirthDate", "19800101")
    ds.PatientSex = over.get("PatientSex", "F")
    ds.StudyInstanceUID = over.get("StudyInstanceUID", generate_uid())
    ds.SeriesInstanceUID = over.get("SeriesInstanceUID", generate_uid())
    ds.StudyDate = over.get("StudyDate", "20200101")
    ds.AccessionNumber = over.get("AccessionNumber", "ACC1")
    ds.Modality = over.get("Modality", "CT")
    ds.SOPClassUID = over.get("SOPClassUID", CTImageStorage)
    ds.SOPInstanceUID = sop_instance or generate_uid()
    ds.Rows = 4
    ds.Columns = 4
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = bytes(4 * 4 * 2)
    for k, v in over.get("extra", {}).items():
        setattr(ds, k, v)
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ds.SOPClassUID
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def save(ds: Dataset, path) -> str:
    ds.save_as(str(path), enforce_file_format=True)
    return str(path)


@pytest.fixture
def ct_factory():
    return make_ct
