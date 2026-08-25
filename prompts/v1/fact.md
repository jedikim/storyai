# Fact and device pass v1

Input is one complete scene segment. Emit typed Fact and Promise nodes using explicit absolute IDs.
Fact props must contain `subject`, `predicate`, and `object`. Promise props use F, T, P and a valid
state. Every item requires an exact evidence span and quote. Never resolve a pronoun or alias unless
the supplied address map binds it unambiguously.
