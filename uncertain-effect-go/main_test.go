package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// The corpus builds one case per fault. These probes hold the shapes it never
// builds, each with a control that must still be allowed, so a reader that
// simply refuses everything fails here too. They mirror the Python suite's
// core set rather than porting all of it; see README.md for what that means.

const digest = "sha256:4444444444444444444444444444444444444444444444444444444444444444"

func base() map[string]any {
	return map[string]any{
		"asOf":       "2026-10-28T12:10:00Z",
		"contractId": contractID,
		"operation": map[string]any{
			"action": "create-order", "argsDigest": digest,
			"id": "op-1", "idempotencyKey": "order-7f3a",
		},
		"effectOwner": map[string]any{
			"deduplicatesByKey": false, "id": "orders-service", "keyRetentionSeconds": nil,
		},
		"attempts": []any{
			map[string]any{
				"attempt": 1, "argsDigest": digest,
				"dispatchedAt":   "2026-10-28T12:00:01Z",
				"idempotencyKey": "order-7f3a", "reply": "timeout",
			},
		},
		"reconciliations": []any{},
		"ownerEffects":    1,
		"reported":        map[string]any{"outcome": "unknown"},
	}
}

func attempt(number int, key, reply, at string) map[string]any {
	return map[string]any{
		"attempt": number, "argsDigest": digest, "dispatchedAt": at,
		"idempotencyKey": key, "reply": reply,
	}
}

func withStop(record map[string]any, overrides map[string]any) map[string]any {
	stop := map[string]any{
		"requestedAt":    "2026-10-28T12:00:09Z",
		"revokedAt":      "2026-10-28T12:00:12Z",
		"acknowledgedAt": "2026-10-28T12:00:10Z",
		"authorityChecks": []any{
			map[string]any{"at": "2026-10-28T12:00:20Z", "result": "refused"},
		},
		"ownerCommit": map[string]any{
			"notBefore": "2026-10-28T12:00:02Z", "notAfter": "2026-10-28T12:00:03Z",
		},
		"reported": "quiescent",
	}
	for key, value := range overrides {
		stop[key] = value
	}
	record["stop"] = stop
	return record
}

type probe struct {
	name     string
	record   map[string]any
	decision string
	reason   string
}

func probes() []probe {
	var out []probe

	add := func(name string, record map[string]any, decision, reason string) {
		out = append(out, probe{name, record, decision, reason})
	}

	// A report the reader cannot read is not the settled outcome.
	for _, mutate := range []func(map[string]any){
		func(r map[string]any) { delete(r, "reported") },
		func(r map[string]any) { r["reported"] = map[string]any{"outcome": nil} },
		func(r map[string]any) { r["reported"] = map[string]any{} },
	} {
		record := base()
		mutate(record)
		add("report unreadable", record, deny, "outcome_disagrees_with_authority")
	}
	add("control: report says unknown", base(), allow, held)

	// A ledger count that is not a count, or is negative, is not "at most one".
	for _, value := range []any{"1", true, 1.5, nil, json.Number("-1")} {
		add("count unreadable", base(), allow, held)
		out[len(out)-1].record["ownerEffects"] = value
		out[len(out)-1].decision, out[len(out)-1].reason = deny, "duplicate_effect"
	}
	absent := base()
	delete(absent, "ownerEffects")
	add("count absent", absent, deny, "duplicate_effect")
	add("control: count 1", base(), allow, held)
	zero := base()
	zero["ownerEffects"] = 0
	add("control: count 0", zero, allow, held)

	// An owner answer about another key settles nothing and covers nothing.
	other := base()
	other["reconciliations"] = []any{map[string]any{
		"at": "2026-10-28T12:00:20Z", "idempotencyKey": "SOMEONE-ELSES-KEY",
		"result": "absent", "final": true,
	}}
	other["attempts"] = []any{
		attempt(1, "order-7f3a", "timeout", "2026-10-28T12:00:01Z"),
		attempt(2, "order-7f3a", "confirmed", "2026-10-28T12:00:40Z"),
	}
	other["reported"] = map[string]any{"outcome": "committed"}
	add("cover from another key", other, deny, "retry_after_unknown")

	mine := base()
	mine["reconciliations"] = []any{map[string]any{
		"at": "2026-10-28T12:00:20Z", "idempotencyKey": "order-7f3a",
		"result": "absent", "final": true,
	}}
	mine["attempts"] = []any{
		attempt(1, "order-7f3a", "timeout", "2026-10-28T12:00:01Z"),
		attempt(2, "order-7f3a", "confirmed", "2026-10-28T12:00:40Z"),
	}
	mine["reported"] = map[string]any{"outcome": "committed"}
	add("control: cover from this key", mine, allow, held)

	// A dispatch claiming a lease with no lease record is unverifiable.
	claimed := base()
	claimedAttempt := claimed["attempts"].([]any)[0].(map[string]any)
	claimedAttempt["worker"] = "w1"
	claimedAttempt["fencingToken"] = 9
	add("lease claimed, no lease record", claimed, deny, "dispatch_beyond_lease")
	add("control: no lease claimed", base(), allow, held)

	// A triggered attempt carries the operation's own key.
	triggered := func(trigger any, key string) map[string]any {
		record := base()
		record["effectOwner"].(map[string]any)["deduplicatesByKey"] = true
		second := attempt(2, key, "confirmed", "2026-10-28T12:00:40Z")
		second["trigger"] = trigger
		record["attempts"] = []any{
			attempt(1, "order-7f3a", "timeout", "2026-10-28T12:00:01Z"),
			second,
		}
		record["reported"] = map[string]any{"outcome": "committed"}
		return record
	}
	add("control: trigger carries this key",
		triggered(map[string]any{"id": "task-2", "kind": "redelivery"}, "order-7f3a"), allow, held)
	add("trigger derives its own key",
		triggered(map[string]any{"id": "task-2", "kind": "redelivery"}, "order-7f3b-task-2"),
		deny, "idempotency_key_follows_trigger")
	add("trigger is not an object",
		triggered("redelivery", "order-7f3a"), deny, "idempotency_key_follows_trigger")
	add("trigger names an unknown kind",
		triggered(map[string]any{"id": "x", "kind": "timer"}, "order-7f3a"),
		deny, "idempotency_key_follows_trigger")

	// The ledger cannot hold more effects than the attempts that could make one.
	orphan := base()
	orphan["attempts"] = []any{}
	orphan["reported"] = map[string]any{"outcome": "not_run"}
	add("effect with no attempt to account for it", orphan, deny, "owner_effect_unaccounted")

	none := base()
	none["attempts"] = []any{attempt(1, "order-7f3a", "no_effect", "2026-10-28T12:00:01Z")}
	none["reported"] = map[string]any{"outcome": "not_run"}
	add("a no-effect reply cannot account for an effect", none, deny, "owner_effect_unaccounted")

	two := base()
	two["effectOwner"].(map[string]any)["deduplicatesByKey"] = true
	two["ownerEffects"] = 2
	two["attempts"] = []any{
		attempt(1, "order-7f3a", "timeout", "2026-10-28T12:00:01Z"),
		attempt(2, "order-7f3a", "confirmed", "2026-10-28T12:00:40Z"),
	}
	two["reported"] = map[string]any{"outcome": "committed"}
	add("two effects, two attempts, names the count", two, deny, "duplicate_effect")

	// The stop block, at its boundaries.
	add("control: clean stop, authority refused", withStop(base(), nil), allow, held)

	acceptedAt := withStop(base(), map[string]any{"authorityChecks": []any{
		map[string]any{"at": "2026-10-28T12:00:12Z", "result": "accepted"},
	}})
	add("authority accepted at the revocation", acceptedAt, deny, "authority_used_after_revocation")

	acceptedBefore := withStop(base(), map[string]any{"authorityChecks": []any{
		map[string]any{"at": "2026-10-28T12:00:11Z", "result": "accepted"},
	}})
	add("authority accepted before the revocation", acceptedBefore, allow, held)

	afterAck := withStop(base(), map[string]any{"ownerCommit": map[string]any{
		"notBefore": "2026-10-28T12:00:15Z", "notAfter": "2026-10-28T12:00:16Z",
	}})
	add("commit wholly after the acknowledgement", afterAck, deny, "effect_after_stop_reported_quiescent")

	afterAckReported := withStop(base(), map[string]any{
		"reported": "effect_after_stop",
		"ownerCommit": map[string]any{
			"notBefore": "2026-10-28T12:00:15Z", "notAfter": "2026-10-28T12:00:16Z",
		},
	})
	add("control: same commit, reported after the stop", afterAckReported, allow, held)

	straddleStart := withStop(base(), map[string]any{"ownerCommit": map[string]any{
		"notBefore": "2026-10-28T12:00:10Z", "notAfter": "2026-10-28T12:00:12Z",
	}})
	add("commit begins at the acknowledgement", straddleStart, deny, "stop_straddle_reported_settled")

	straddleEnd := withStop(base(), map[string]any{"ownerCommit": map[string]any{
		"notBefore": "2026-10-28T12:00:08Z", "notAfter": "2026-10-28T12:00:10Z",
	}})
	add("commit ends at the acknowledgement", straddleEnd, deny, "stop_straddle_reported_settled")

	notEstablished := withStop(base(), map[string]any{
		"reported": stopNotEstablished,
		"ownerCommit": map[string]any{
			"notBefore": "2026-10-28T12:00:08Z", "notAfter": "2026-10-28T12:00:12Z",
		},
	})
	add("control: straddle reported not established", notEstablished, allow, held)

	noWindow := withStop(base(), map[string]any{"ownerCommit": nil})
	add("control: stop states no commit window", noWindow, allow, held)

	// The remaining checks, so each of the fifteen has a probe of its own rather
	// than relying on the corpus to reach it.
	noContract := base()
	delete(noContract, "contractId")
	add("contract id absent", noContract, deny, "contract_id_missing")

	wrongContract := base()
	wrongContract["contractId"] = "https://example.invalid/other"
	add("contract id unknown", wrongContract, deny, "contract_id_unknown")

	noKey := base()
	noKeyAttempt := noKey["attempts"].([]any)[0].(map[string]any)
	delete(noKeyAttempt, "idempotencyKey")
	add("attempt names no key", noKey, deny, "operation_identity_missing")

	notAnObject := base()
	notAnObject["attempts"] = []any{nil}
	add("attempt entry is not a record", notAnObject, deny, "operation_identity_missing")

	otherArgs := base()
	otherArgsAttempt := otherArgs["attempts"].([]any)[0].(map[string]any)
	otherArgsAttempt["argsDigest"] = "sha256:" + "5" + digest[len("sha256:")+1:]
	add("key reused with other arguments", otherArgs, deny, "idempotency_key_args_changed")

	released := base()
	released["reservation"] = map[string]any{"units": 5, "expiresAt": "2026-10-28T12:02:00Z", "charged": false}
	add("reservation released while unknown", released, deny, "unknown_released")

	charged := base()
	charged["reservation"] = map[string]any{"units": 5, "expiresAt": "2026-10-28T12:02:00Z", "charged": true}
	add("control: reservation still charged", charged, allow, held)

	settled := base()
	settled["reported"] = map[string]any{"outcome": "not_run"}
	add("unknown reported as not run", settled, deny, "outcome_settled_without_authority")

	return out
}

func TestProbes(t *testing.T) {
	dir := t.TempDir()
	t.Logf("probes: %d", len(probes()))
	for _, p := range probes() {
		path := filepath.Join(dir, "record.json")
		encoded, err := json.Marshal(p.record)
		if err != nil {
			t.Fatalf("%s: cannot encode the probe: %v", p.name, err)
		}
		if err := os.WriteFile(path, encoded, 0o600); err != nil {
			t.Fatalf("%s: cannot write the probe: %v", p.name, err)
		}
		trail, err := load(path)
		if err != nil {
			t.Fatalf("%s: cannot load the probe: %v", p.name, err)
		}
		decision, reason := judge(trail)
		if decision != p.decision || reason != p.reason {
			t.Errorf("%s: got %s/%s, want %s/%s", p.name, decision, reason, p.decision, p.reason)
		}
	}
}

// Every reason the reader can name must have a predicate, and every predicate
// must be reachable from the ordered list, or a check is dead code.
func TestEveryCheckIsWired(t *testing.T) {
	if len(firstFailing) != 15 {
		t.Fatalf("expected 15 ordered checks, found %d", len(firstFailing))
	}
	if len(checks) != len(firstFailing) {
		t.Fatalf("checks map has %d entries for %d reasons", len(checks), len(firstFailing))
	}
	for _, reason := range firstFailing {
		if _, ok := checks[reason]; !ok {
			t.Errorf("reason %s has no predicate", reason)
		}
	}
}
