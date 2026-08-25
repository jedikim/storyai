from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from server.core.database import initialize_database
from server.core.ontology import Ontology
from server.core.service import StoryService
from server.load_bible import BibleLoader

CHARACTER = """---
id: character/한도영
title: 한도영
summary: 등대지기. 3장부터 용의선상.
aliases: [도영, 등대지기]
tags: [POV, Suspect]
props:
  역할: 등대지기
  주손: 왼손
story_from: 1
reveal_at: 1
---

# 한도영

왼손잡이 등대지기다.
"""

SCENE = """---
id: scene/A1.C03.S01
kind: Scene
title: 젖은 장갑
summary: 한도영이 부두에서 젖은 장갑을 발견한다.
story_from: 3
reveal_at: 3
edges:
  - rel: present_at
    to: character/한도영
    story_from: 3
  - rel: contains
    to: object/젖은장갑
  - rel: mentioned_in
    to: character/한도영
---

# 젖은 장갑

한도영은 부두 끝에서 젖은 장갑을 집어 들었다.
"""

OBJECT = """---
id: object/젖은장갑
title: 젖은 장갑
summary: 부두에서 발견된 정체불명의 장갑.
aliases: [장갑]
story_from: 3
reveal_at: 3
---

# 젖은 장갑

검은 가죽 장갑이며 바닷물에 젖어 있다.
"""

HIDDEN = """---
id: promise/숨은열쇠
title: 숨은 열쇠
summary: 5장에서 처음 드러나는 복선.
reveal_at: 5
props:
  status: hypothetical
---

# 숨은 열쇠

아직 독자에게 공개되지 않았다.
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "storyai"
    (root / "spec").mkdir(parents=True)
    for folder in ("manuscript", "store"):
        (root / folder).mkdir()
    source_spec = Path(__file__).resolve().parents[1] / "spec"
    for name in ("schema.sql", "ontology.json", "rules.json", "policy.json", "tools.json"):
        shutil.copy2(source_spec / name, root / "spec" / name)
    for folder in ("characters", "scenes", "objects", "promises"):
        (root / "bible" / folder).mkdir(parents=True)
    (root / "bible" / "characters" / "한도영.md").write_text(CHARACTER, encoding="utf-8")
    (root / "bible" / "scenes" / "A1.C03.S01.md").write_text(SCENE, encoding="utf-8")
    (root / "bible" / "objects" / "젖은장갑.md").write_text(OBJECT, encoding="utf-8")
    (root / "bible" / "promises" / "숨은열쇠.md").write_text(HIDDEN, encoding="utf-8")
    return root


@pytest.fixture()
def service(project: Path) -> StoryService:
    database = initialize_database(project / "store" / "story.db", project / "spec" / "schema.sql")
    ontology = Ontology.load(project / "spec" / "ontology.json")
    result = BibleLoader(
        project_root=project,
        bible_root=project / "bible",
        db_path=database,
        ontology=ontology,
    ).load()
    assert result == {"nodes": 4, "edges": 3}
    return StoryService(
        project_root=project,
        db_path=database,
        ontology_path=project / "spec" / "ontology.json",
        rules_path=project / "spec" / "rules.json",
        schema_path=project / "spec" / "schema.sql",
    )
