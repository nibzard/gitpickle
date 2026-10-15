-------------------------- MODULE ArchiveCommit --------------------------
EXTENDS Naturals, FiniteSets
CONSTANTS Objects, Points, Attempts, Unsafe
VARIABLES durable, manifests, published, evidence, verified, staged, source, replies
vars == <<durable, manifests, published, evidence, verified, staged, source, replies>>
\* Each point includes all required Git, LFS, wiki, and transitive base IDs.
Deps(p) == Objects
Init == /\ durable = {} /\ manifests = {} /\ published = {}
        /\ evidence = {} /\ verified = {} /\ staged = {}
        /\ source = TRUE /\ replies = {}
Capture(a) == /\ source /\ staged' = staged \cup {a}
              /\ UNCHANGED <<durable, manifests, published, evidence, verified, source, replies>>
Upload(a,o) == /\ a \in staged /\ durable' = durable \cup {o}
               /\ UNCHANGED <<manifests, published, evidence, verified, staged, source, replies>>
Manifest(p) == /\ (Unsafe \/ Deps(p) \subseteq durable)
               /\ manifests' = manifests \cup {p}
               /\ UNCHANGED <<durable, published, evidence, verified, staged, source, replies>>
Publish(p) == /\ p \in manifests /\ published' = published \cup {p}
              /\ UNCHANGED <<durable, manifests, evidence, verified, staged, source, replies>>
Reply(p) == /\ p \in published /\ replies' = replies \cup {p}
            /\ UNCHANGED <<durable, manifests, published, evidence, verified, staged, source>>
Readback(p) == /\ p \in published /\ Deps(p) \subseteq durable
               /\ evidence' = evidence \cup {p}
               /\ UNCHANGED <<durable, manifests, published, verified, staged, source, replies>>
Verify(p) == /\ p \in evidence /\ verified' = verified \cup {p}
             /\ UNCHANGED <<durable, manifests, published, evidence, staged, source, replies>>
Crash == /\ staged' = {} /\ UNCHANGED <<durable, manifests, published, evidence, verified, source, replies>>
SourceLoss == /\ source' = FALSE
              /\ UNCHANGED <<durable, manifests, published, evidence, verified, staged, replies>>
Next == (\E a \in Attempts: Capture(a)) \/ (\E a \in Attempts, o \in Objects: Upload(a,o))
        \/ (\E p \in Points: Manifest(p) \/ Publish(p) \/ Reply(p) \/ Readback(p) \/ Verify(p))
        \/ Crash \/ SourceLoss
Spec == Init /\ [][Next]_vars
TypeOK == /\ durable \subseteq Objects /\ manifests \subseteq Points
          /\ published \subseteq Points /\ evidence \subseteq Points
          /\ verified \subseteq Points /\ staged \subseteq Attempts
          /\ source \in BOOLEAN /\ replies \subseteq Points
CompletePointIsClosed == \A p \in published: p \in manifests /\ Deps(p) \subseteq durable
VerificationIsEarned == verified \subseteq evidence /\ evidence \subseteq published
AcknowledgmentIsDurable == replies \subseteq published
SourceLossDoesNotEraseHistory == [][published \subseteq published']_vars
=============================================================================
