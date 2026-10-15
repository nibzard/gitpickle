-------------------------- MODULE PlanApproval --------------------------
EXTENDS Naturals, FiniteSets
CONSTANT Unsafe
\* Each bit represents a difference from the immutable approved snapshot.
Fields == {"actor", "repositories", "destination", "visibility", "versions",
           "digest", "expiry", "impact", "operation"}
VARIABLES changed, approval, grant, version, now, queued, used, executions, invalid
vars == <<changed, approval, grant, version, now, queued, used, executions, invalid>>
Init == /\ changed = {} /\ approval = FALSE /\ grant = TRUE /\ version = TRUE
        /\ now = 0 /\ queued = FALSE /\ used = FALSE /\ executions = 0 /\ invalid = FALSE
Valid == approval /\ changed = {} /\ grant /\ version /\ now < 2 /\ ~used
Approve == /\ ~approval /\ changed = {} /\ grant /\ version /\ now < 2
           /\ approval' = TRUE
           /\ UNCHANGED <<changed, grant, version, now, queued, used, executions, invalid>>
Change == /\ \E field \in Fields: changed' = changed \cup {field}
          /\ UNCHANGED <<approval, grant, version, now, queued, used, executions, invalid>>
Revoke == /\ grant' = FALSE
          /\ UNCHANGED <<changed, approval, version, now, queued, used, executions, invalid>>
AdvanceVersion == /\ version' = FALSE
                  /\ UNCHANGED <<changed, approval, grant, now, queued, used, executions, invalid>>
Tick == /\ now < 2 /\ now' = now + 1
        /\ UNCHANGED <<changed, approval, grant, version, queued, used, executions, invalid>>
Queue == /\ Valid /\ queued' = TRUE
         /\ UNCHANGED <<changed, approval, grant, version, now, used, executions, invalid>>
Execute == /\ queued /\ executions < 2 /\ (Valid \/ Unsafe)
           /\ executions' = executions + 1 /\ used' = TRUE
           /\ invalid' = (invalid \/ ~Valid)
           /\ UNCHANGED <<changed, approval, grant, version, now, queued>>
\* A model-provided boolean can request execution but cannot create authority.
ModelBoolean == /\ ~approval /\ Unsafe /\ queued' = TRUE
                /\ UNCHANGED <<changed, approval, grant, version, now, used, executions, invalid>>
\* Process loss loses the queue, never the durable use record.
Crash == /\ queued' = FALSE
         /\ UNCHANGED <<changed, approval, grant, version, now, used, executions, invalid>>
Next == Approve \/ Change \/ Revoke \/ AdvanceVersion \/ Tick \/ Queue \/ Execute \/ ModelBoolean \/ Crash
Spec == Init /\ [][Next]_vars
TypeOK == /\ changed \subseteq Fields /\ approval \in BOOLEAN /\ grant \in BOOLEAN
          /\ version \in BOOLEAN /\ now \in 0..2 /\ queued \in BOOLEAN
          /\ used \in BOOLEAN /\ executions \in 0..2 /\ invalid \in BOOLEAN
ApprovalMatchesExecution == ~invalid
SingleUse == executions <= 1
=============================================================================
