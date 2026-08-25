# Extraction prompts v1

`v1` is immutable. A changed contract gets a new sibling version instead of overwriting these
files. Every pass receives one complete scene segment and its exact UTF-8 byte range; callers must
not truncate the segment.

The extractor writes a `<chapter>.story.json` binding manifest beside the manuscript. `ingest`
validates the manifest and turns it into a Proposal. It never commits automatically.

Required guarantees:

- every node and edge endpoint is an absolute typed story ID;
- every extracted node has one or more exact evidence byte spans;
- quoted bytes must equal the source bytes at the declared span;
- unknown coreference is reported as unresolved, never guessed;
- JSON conforms to `manifest.schema.json`.
