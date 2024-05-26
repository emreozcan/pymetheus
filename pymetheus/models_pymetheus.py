from dataclasses import dataclass

from .models_zotero import ItemType
from .zotero_csl_interop import ZoteroFieldName, ZoteroCreatorTypeName


@dataclass(frozen=True)
class NameData:
    family: str | None = None
    given: str | None = None
    suffix: str | None = None
    dropping_particle: str | None = None
    non_dropping_particle: str | None = None
    literal: str | None = None

    def as_dict(self) -> dict:
        new_obj = {}

        if self.family is not None:
            new_obj['family'] = self.family
        if self.given is not None:
            new_obj['given'] = self.given
        if self.suffix is not None:
            new_obj['suffix'] = self.suffix
        if self.dropping_particle is not None:
            new_obj['dropping-particle'] = self.dropping_particle
        if self.non_dropping_particle is not None:
            new_obj['non-dropping-particle'] = self.non_dropping_particle
        if self.literal is not None:
            new_obj['literal'] = self.literal

        return new_obj


@dataclass
class Item:
    type: ItemType
    field_data: dict[ZoteroFieldName, str]
    creators: dict[ZoteroCreatorTypeName, list[NameData]]
