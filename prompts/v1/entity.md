# Entity and relation pass v1

Input is one complete scene segment plus an address map. Bind every entity mention and every edge
endpoint to an existing or explicitly created absolute typed ID. Return unresolved mentions instead
of inventing coreference. Soft prose mentions use `mentioned_in`; structural relations use their
ontology relation. Every new node requires exact evidence.
