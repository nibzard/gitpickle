---------------------------- MODULE GitPublish ----------------------------
EXTENDS Naturals, FiniteSets
CONSTANTS Objects, Refs, Attempts, Limit, Unsafe
VARIABLES durable, manifests, published, visible, staged, replies
vars == <<durable, manifests, published, visible, staged, replies>>
Generations == 1..Limit
Results == [generation : Generations, refs : SUBSET Refs,
            atomic : BOOLEAN, rejected : SUBSET Refs]
Stages == [result : Results, attempt : Attempts]
Valid(r) == /\ r.refs \cap r.rejected = {}
            /\ (r.atomic /\ r.rejected # {} => r.refs = {})
Deps(g) == {<<g,o>> : o \in Objects}
Init == /\ durable = {} /\ manifests = {} /\ published = {}
        /\ visible = {} /\ staged = {} /\ replies = {}
Stage(s) == /\ staged = {} /\ Valid(s.result)
            /\ s.result.generation = Cardinality(published) + 1
            /\ staged' = {s}
            /\ UNCHANGED <<durable, manifests, published, visible, replies>>
Upload(s,o) == /\ s \in staged
               /\ durable' = durable \cup {<<s.result.generation,o>>}
               /\ UNCHANGED <<manifests, published, visible, staged, replies>>
Manifest(s) == /\ s \in staged /\ Deps(s.result.generation) \subseteq durable
               /\ \A r \in manifests: r.generation = s.result.generation => r = s.result
               /\ manifests' = manifests \cup {s.result}
               /\ UNCHANGED <<durable, published, visible, staged, replies>>
Publish(s) == /\ s \in staged /\ s.result \in manifests
              /\ published' = published \cup {s.result}
              /\ UNCHANGED <<durable, manifests, visible, staged, replies>>
Expose == /\ visible' = published
          /\ UNCHANGED <<durable, manifests, published, staged, replies>>
Reply(r) == /\ (r \in visible \/ (Unsafe /\ r.refs # {} /\ \E s \in staged: r = s.result))
            /\ replies' = replies \cup {r}
            /\ UNCHANGED <<durable, manifests, published, visible, staged>>
\* A restart reconstructs visible refs from storage, never from staging.
Crash == /\ staged' = {} /\ visible' = {}
         /\ UNCHANGED <<durable, manifests, published, replies>>
\* Retry can replay the immutable result after publication and a lost reply.
Retry(r,a) == /\ r \in published /\ a \in Attempts
              /\ visible' = published /\ staged' = {}
              /\ UNCHANGED <<durable, manifests, published, replies>>
Next == (\E s \in Stages: Stage(s) \/ Manifest(s) \/ Publish(s))
        \/ (\E s \in Stages, o \in Objects: Upload(s,o))
        \/ (\E r \in Results: Reply(r))
        \/ (\E r \in Results, a \in Attempts: Retry(r,a)) \/ Expose \/ Crash
Spec == Init /\ [][Next]_vars
TypeOK == /\ durable \subseteq (Generations \X Objects)
          /\ manifests \subseteq Results /\ published \subseteq Results
          /\ visible \subseteq Results /\ replies \subseteq Results
          /\ staged \subseteq Stages /\ Cardinality(staged) <= 1
PublishedClosure == \A r \in published: r \in manifests /\ Deps(r.generation) \subseteq durable
AcknowledgedPushIsDurable == replies \subseteq published
ReadersSeeOnlyPublished == visible \subseteq published
GitResultsArePreserved == \A r \in (published \cup replies): Valid(r)
ReplayIsConsistent == \A r,t \in manifests: r.generation = t.generation => r = t
HistorySurvivesCrash == [][published \subseteq published']_vars
=============================================================================
