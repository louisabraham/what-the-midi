"""MIDI collections to download.

The order is the priority: when 2 files have the same melody, the index
keeps the name from the first source. Sources with good file names
(artist and title) come first.
"""

from dataclasses import dataclass, field


@dataclass
class Source:
    name: str
    title: str
    homepage: str
    license: str
    # direct URLs, or files of an archive.org item / a Hugging Face dataset
    urls: list[str] = field(default_factory=list)
    archive_org: str | None = None  # item id; downloads its original files
    archive_org_pattern: str = r"\.(zip|7z|mid|midi)$"
    huggingface: str | None = None  # dataset id
    huggingface_files: list[str] = field(default_factory=list)
    gated: bool = False  # needs a Hugging Face token (HF_TOKEN) with access granted
    notes: str = ""


HF = "https://huggingface.co/datasets"

SOURCES = [
    Source(
        name="lakh_clean",
        title="Lakh MIDI Dataset, clean_midi subset",
        homepage="https://colinraffel.com/projects/lmd/",
        license="CC-BY 4.0",
        urls=["http://hog.ee.columbia.edu/craffel/lmd/clean_midi.tar.gz"],
        notes="17k files named Artist/Title.mid",
    ),
    Source(
        name="midiman",
        title="MIDIMAN Melody Kit 1.0 (reddit.com/r/WeAreTheMusicMakers/comments/3ajwe4)",
        homepage="https://archive.org/details/midiman_melody_kit_1.0_2015-06",
        license="unknown (scraped from ~200 websites in 2015)",
        archive_org="midiman_melody_kit_1.0_2015-06",
        archive_org_pattern=r"\.zip$",
        notes="130k files, deduplicated by checksum by its author",
    ),
    Source(
        name="magic_of_midi",
        title="The Magic of MIDI V1",
        homepage="https://archive.org/details/themagicofmidiv1",
        license="unknown",
        archive_org="themagicofmidiv1",
        archive_org_pattern=r"\.7z$",
        notes="169k files, includes the MIDIMAN collection",
    ),
    Source(
        name="lakh_full",
        title="Lakh MIDI Dataset, full",
        homepage="https://colinraffel.com/projects/lmd/",
        license="CC-BY 4.0",
        urls=["http://hog.ee.columbia.edu/craffel/lmd/lmd_full.tar.gz"],
        notes="178k files named by MD5",
    ),
    Source(
        name="los_angeles",
        title="Los Angeles MIDI Dataset 4.0",
        homepage=f"{HF}/projectlosangeles/Los-Angeles-MIDI-Dataset",
        license="CC-BY-NC-SA 4.0",
        huggingface="projectlosangeles/Los-Angeles-MIDI-Dataset",
        huggingface_files=["Los-Angeles-MIDI-Dataset-Ver-4-0-CC-BY-NC-SA.zip"],
        notes="405k files, deduplicated by its author",
    ),
    Source(
        name="gigamidi",
        title="GigaMIDI 2.0",
        homepage=f"{HF}/Metacreation/GigaMIDI",
        license="research use (Canadian fair dealing)",
        huggingface="Metacreation/GigaMIDI",
        huggingface_files=["Final_GigaMIDI_V2.0_Final.zip"],
        gated=True,
        notes="2.1M files; accept the terms on the dataset page first",
    ),
    Source(
        name="bread",
        title="bread-midi-dataset",
        homepage=f"{HF}/breadlicker45/bread-midi-dataset",
        license="unknown (card: 'anyone can use for any use case')",
        huggingface="breadlicker45/bread-midi-dataset",
        huggingface_files=["bread-midi-dataset.zip"],
        notes="851k files",
    ),
    Source(
        name="monster",
        title="Monster MIDI Dataset 1.0",
        homepage=f"{HF}/projectlosangeles/Monster-MIDI-Dataset",
        license="CC-BY-NC-SA 4.0",
        huggingface="projectlosangeles/Monster-MIDI-Dataset",
        huggingface_files=["Monster-MIDI-Dataset-Ver-1-0-CC-BY-NC-SA.zip"],
        notes="1M+ files, rewritten by its author: MD5s differ from the originals",
    ),
    Source(
        name="sourdough",
        title="Sourdough MIDI dataset",
        homepage=f"{HF}/BreadAi/Sourdough-midi-dataset",
        license="unknown",
        huggingface="BreadAi/Sourdough-midi-dataset",
        huggingface_files=["archive.zip"],
        notes="5M files (claimed), deduplicated with fdupes",
    ),
]

BY_NAME = {s.name: s for s in SOURCES}
