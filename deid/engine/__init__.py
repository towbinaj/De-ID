"""De-ID engine - vendored from the DICOM Toolkit and extended here.

``processing.py`` and ``anon_profiles.py`` were copied from the DICOM Toolkit's
proven de-identification engine and evolve independently in this app (De-ID adds
object/series drop rules, a persistent pseudonym index, OCR redaction, and CTP
anonymizer-script compatibility). Keeping its own copy is deliberate: this
engine grows features the Toolkit may never need.
"""
