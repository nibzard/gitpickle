---------------------------- MODULE JobReplay ----------------------------
EXTENDS Naturals, FiniteSets
CONSTANTS Jobs, Arguments, Attempts, Unsafe
VARIABLES accepted, outcomes, running, replies, terminalReplies, rejected, source
vars == <<accepted, outcomes, running, replies, terminalReplies, rejected, source>>
Init == /\ accepted = [j \in Jobs |-> {}] /\ outcomes = [j \in Jobs |-> {}]
        /\ running = {} /\ replies = {} /\ terminalReplies = {} /\ rejected = {}
        /\ source = TRUE
\* Jobs abstract (actor, operation, idempotency key); arguments include scope.
Accept(j,d) == /\ accepted[j] = {} 
               /\ accepted' = [accepted EXCEPT ![j] = {d}]
               /\ UNCHANGED <<outcomes, running, replies, terminalReplies, rejected, source>>
Deliver(j,d,a) == /\ d \in accepted[j]
                  /\ running' = running \cup {<<j,d,a>>}
                  /\ UNCHANGED <<accepted, outcomes, replies, terminalReplies, rejected, source>>
Reject(j,d) == /\ accepted[j] # {} /\ d \notin accepted[j]
               /\ rejected' = rejected \cup {<<j,d>>}
               /\ UNCHANGED <<accepted, outcomes, running, replies, terminalReplies, source>>
AcceptReply(j) == /\ accepted[j] # {} /\ replies' = replies \cup {j}
                  /\ UNCHANGED <<accepted, outcomes, running, terminalReplies, rejected, source>>
Commit(j,d,a) == /\ <<j,d,a>> \in running
                 /\ (outcomes[j] = {} \/ d \in outcomes[j] \/ Unsafe)
                 /\ outcomes' = [outcomes EXCEPT ![j] = @ \cup {IF Unsafe THEN a ELSE d}]
                 /\ UNCHANGED <<accepted, running, replies, terminalReplies, rejected, source>>
TerminalReply(j) == /\ outcomes[j] # {} /\ terminalReplies' = terminalReplies \cup {j}
                    /\ UNCHANGED <<accepted, outcomes, running, replies, rejected, source>>
Crash == /\ running' = {} /\ UNCHANGED <<accepted, outcomes, replies, terminalReplies, rejected, source>>
SourceLoss == /\ source' = FALSE
              /\ UNCHANGED <<accepted, outcomes, running, replies, terminalReplies, rejected>>
Next == (\E j \in Jobs, d \in Arguments: Accept(j,d) \/ Reject(j,d)
          \/ (\E a \in Attempts: Deliver(j,d,a) \/ Commit(j,d,a)))
        \/ (\E j \in Jobs: AcceptReply(j) \/ TerminalReply(j)) \/ Crash \/ SourceLoss
Spec == Init /\ [][Next]_vars
TypeOK == /\ accepted \in [Jobs -> SUBSET Arguments]
          /\ outcomes \in [Jobs -> SUBSET (Arguments \cup Attempts)]
          /\ running \subseteq (Jobs \X Arguments \X Attempts)
          /\ replies \subseteq Jobs /\ terminalReplies \subseteq Jobs
          /\ rejected \subseteq (Jobs \X Arguments) /\ source \in BOOLEAN
ReplayIsConsistent == \A j \in Jobs: Cardinality(outcomes[j]) <= 1 /\ outcomes[j] \subseteq accepted[j]
AcceptanceIsDurable == \A j \in replies: accepted[j] # {}
TerminalIsDurable == \A j \in terminalReplies: outcomes[j] # {}
\* Conditional liveness: all requests already durable, no crashes or outages.
LiveInit == /\ accepted = [j \in Jobs |-> {CHOOSE d \in Arguments: TRUE}]
            /\ outcomes = [j \in Jobs |-> {}] /\ running = {}
            /\ replies = Jobs /\ terminalReplies = {} /\ rejected = {}
            /\ source = TRUE
Work(j) == \E d \in Arguments, a \in Attempts: Deliver(j,d,a) \/ Commit(j,d,a)
LiveNext == \E j \in Jobs: Work(j)
LiveSpec == LiveInit /\ [][LiveNext]_vars /\ (\A j \in Jobs: WF_vars(Work(j)))
EventuallyReconciled == \A j \in Jobs: <>(outcomes[j] # {})
=============================================================================
