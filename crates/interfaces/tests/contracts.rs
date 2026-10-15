use gitpickle_application::{
    PageRequest,
    domain::{ErrorCode, git_access::*, identity::*, protection::*},
};
use gitpickle_interfaces::{machine::*, negotiation::*};

#[test]
fn negotiate_versions_and_capabilities_before_mutation() {
    let client = Hello {
        software_version: "0.1".into(),
        management: VersionRange {
            minimum: 1,
            maximum: 3,
        },
        machine_result: VersionRange {
            minimum: 1,
            maximum: 1,
        },
        capabilities: vec![],
    };
    let mut server = client.clone();
    server.software_version = "9.0".into();
    server.management.maximum = 2;
    server.capabilities = vec!["status".into()];
    assert_eq!(
        negotiate(&client, &server, &["status"]),
        Ok(Negotiated {
            management: 2,
            machine_result: 1
        })
    );
    assert_eq!(
        negotiate(&client, &server, &["hosted_push"]),
        Err(ErrorCode::UnsupportedCapability)
    );
    server.machine_result.minimum = 2;
    server.machine_result.maximum = 2;
    assert_eq!(
        negotiate(&client, &server, &[]),
        Err(ErrorCode::UnsupportedVersion)
    );
    server.management.minimum = 4;
    assert_eq!(
        negotiate(&client, &server, &[]),
        Err(ErrorCode::InvalidInput)
    );
}
#[test]
fn evidence_requires_matching_complete_fresh_coverage() {
    let policy = ProtectionPolicy::default();
    let mut status = ProtectionStatus {
        repository: RepositoryId::new("repo_1").unwrap(),
        source_observed_at: Some(Timestamp(100_000)),
        content_changed_at: Some(Timestamp(1)),
        capture_completed_at: Some(Timestamp(2)),
        verified_at: Some(Timestamp(3)),
        restore_tested_at: None,
        point: Some(RecoveryPointId::new("point_1").unwrap()),
        source_available: true,
        matches_observed_state: true,
        coverage: [Component::Git, Component::Lfs]
            .into_iter()
            .map(|component| ComponentCoverage {
                component,
                required: true,
                state: CoverageState::Verified,
                evidence: vec![EvidenceId::new("evidence_1").unwrap()],
            })
            .collect(),
    };
    assert!(status.current_and_verified(Timestamp(100_000 + 8 * 3600), &policy));
    assert!(!status.current_and_verified(Timestamp(100_000 + 8 * 3600 + 1), &policy));
    assert!(!status.current_and_verified(Timestamp(99_999), &policy));
    status.coverage[1].state = CoverageState::Missing;
    assert!(!status.current_and_verified(Timestamp(100_001), &policy));
    status.coverage[1].state = CoverageState::Verified;
    status.matches_observed_state = false;
    assert!(!status.current_and_verified(Timestamp(100_001), &policy));
    status.matches_observed_state = true;
    status.source_available = false;
    assert!(!status.current_and_verified(Timestamp(100_001), &policy));
}
#[test]
fn modes_preserve_authority_and_ids_preserve_kinds() {
    assert_eq!(
        RepositoryMode::ProtectedMirror.require_push(true),
        Err(ErrorCode::ReadOnly)
    );
    assert_eq!(
        RepositoryMode::Hosted.require_source_update(),
        Err(ErrorCode::Conflict)
    );
    assert_eq!(
        RepositoryMode::Hosted.require_push(false),
        Err(ErrorCode::UnsupportedCapability)
    );
    assert!(RepositoryMode::Hosted.require_push(true).is_ok());
    assert!(RepositoryId::new("owner/repo").is_err());
    assert!(ArchiveObjectId::sha256("etag").is_err());
    assert!(GitObjectId::new(GitHashAlgorithm::Sha1, "a".repeat(40)).is_ok());
    assert!(GitObjectId::new(GitHashAlgorithm::Sha256, "a".repeat(40)).is_err());
    assert!(PageRequest::new(0, None).is_err());
    assert!(PageRequest::new(101, None).is_err());
}
#[test]
fn acceptance_transcripts_round_trip_and_obey_exit_contract() {
    let scenarios: serde_json::Value = serde_json::from_str(include_str!(
        "../../../docs/product/transcripts/contracts.json"
    ))
    .unwrap();
    let scenarios = scenarios.as_array().unwrap();
    let names: Vec<_> = scenarios
        .iter()
        .map(|s| s["name"].as_str().unwrap())
        .collect();
    assert_eq!(
        names,
        [
            "protection accepted",
            "unchanged source is current",
            "incomplete coverage query",
            "incomplete coverage health check",
            "storage only recovery preview",
            "verified recovery",
            "mirror rejects push",
            "hosted generation committed"
        ]
    );
    assert_eq!(scenarios[0]["result"]["outcome"], "queued");
    assert_eq!(
        scenarios[0]["result"]["data"]["capture_interval_seconds"],
        21600
    );
    assert_eq!(
        scenarios[1]["result"]["data"]["restore_tested_repositories"],
        0
    );
    assert_eq!(scenarios[4]["result"]["data"]["requires_source"], false);
    assert_eq!(scenarios[4]["result"]["data"]["overwrite"], false);
    assert_eq!(scenarios[5]["result"]["outcome"], "verified");
    assert_eq!(
        scenarios[7]["result"]["data"]["source_updates_paused"],
        true
    );
    for scenario in scenarios {
        let result: ResultV1<serde_json::Value> =
            serde_json::from_value(scenario["result"].clone()).unwrap();
        let line = result.to_json_line().unwrap();
        assert_eq!(
            ResultV1::<serde_json::Value>::from_json(&line).unwrap(),
            result
        );
        assert_eq!(line.lines().count(), 1);
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&line).unwrap(),
            scenario["result"]
        );
        if scenario["command"].as_str().unwrap().contains("status") {
            assert_eq!(
                status_exit(
                    scenario["check"].as_bool().unwrap(),
                    scenario["healthy"].as_bool().unwrap(),
                    None
                ),
                scenario["exit"].as_u64().unwrap() as u8
            );
        }
        if result.outcome == Outcome::Failed {
            assert_eq!(scenario["exit"], 4);
            assert_eq!(result.error.unwrap().code, ErrorCode::ReadOnly.as_str());
        }
    }
}
#[test]
fn invalid_results_never_serialize_as_success() {
    let mut result = ResultV1::<()> {
        schema_version: 1,
        observed_at_unix_seconds: 1,
        scope: Scope {
            repository_ids: vec![],
            archive_id: None,
        },
        outcome: Outcome::Queued,
        job_id: None,
        coverage_gaps: vec![],
        evidence_ids: vec![],
        next_action: None,
        data: None,
        error: None,
    };
    assert_eq!(result.to_json_line(), Err(ErrorCode::InvalidInput));
    result.job_id = Some("job_1".into());
    assert!(result.to_json_line().is_ok());
    result.outcome = Outcome::Verified;
    assert_eq!(result.to_json_line(), Err(ErrorCode::InvalidInput));
    result.evidence_ids.push("evidence_1".into());
    assert!(result.to_json_line().is_ok());
    result.schema_version = 2;
    assert_eq!(result.to_json_line(), Err(ErrorCode::UnsupportedVersion));
    assert_eq!(
        status_exit(false, false, Some(ErrorCode::Authentication)),
        3
    );
    assert_eq!(status_exit(false, false, None), 0);
    assert_eq!(status_exit(true, false, None), 6);
}

#[test]
fn decoder_checks_version_before_interpreting_unknown_data() {
    assert_eq!(
        ResultV1::<()>::from_json(r#"{"schema_version":2,"outcome":"future"}"#),
        Err(ErrorCode::UnsupportedVersion)
    );
    assert_eq!(
        ResultV1::<()>::from_json("not json"),
        Err(ErrorCode::InvalidInput)
    );
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../docs/product/transcripts/contracts.json"
    ))
    .unwrap();
    let mut result = fixture[0]["result"].clone();
    result["future_field"] = true.into();
    assert!(ResultV1::<serde_json::Value>::from_json(&result.to_string()).is_ok());
    result["outcome"] = "future".into();
    assert_eq!(
        ResultV1::<serde_json::Value>::from_json(&result.to_string()),
        Err(ErrorCode::InvalidInput)
    );
}
