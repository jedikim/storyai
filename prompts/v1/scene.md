# Scene boundary pass v1

Input is a complete chapter with UTF-8 byte offsets. Split only when time, location, or the active
character set changes materially. Preserve every byte in exactly one segment. Return explicit Scene
IDs, titles, story/reveal positions, and exact spans. Do not infer entity identity; use only IDs from
the supplied address map and put unresolved mentions in `unresolved`.
