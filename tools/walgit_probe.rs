//! Bounded adoption probe. This is not a GitPickle hosting endpoint.
use std::{path::PathBuf, sync::Arc};
use tokio::io::{AsyncBufReadExt, BufReader};
use walgit_config::{AuthMode, Config, StaticToken, StoreBackend};
use walgit_server::{AppState, router};
use walgit_store::{fault::{FaultPlan, FaultStore}, memory::MemoryStore};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let root = PathBuf::from(std::env::args_os().nth(1).expect("cache path"));
    let token = std::env::var("GITPICKLE_SPIKE_TOKEN")?;
    let store = FaultStore::new(MemoryStore::shared(), "gitpickle-spike", 8);
    let mut input = BufReader::new(tokio::io::stdin()).lines();
    let mut generation = 0;
    loop {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        let address = listener.local_addr()?;
        let mut cfg = Config::default();
        cfg.server.listen = address;
        cfg.server.public_url = Some(format!("http://{address}"));
        cfg.server.auth.mode = AuthMode::Token;
        cfg.server.auth.anonymous_read = false;
        cfg.server.auth.tokens = vec![StaticToken {
            principal: "spike".into(), token: token.clone(), token_env: None,
            write: true, admin: true,
        }];
        cfg.telemetry.metrics = false;
        cfg.store.backend = StoreBackend::Memory;
        cfg.store.bucket = "spike".into();
        cfg.cache.dir = root.join(format!("cache-{generation}"));
        cfg.wal.freshness_ttl = std::time::Duration::ZERO;
        cfg.wal.fsck_objects = true;
        cfg.wal.check_connectivity = true;
        cfg.validate()?;
        let state = AppState::new(Arc::new(cfg), store.clone()).await?;
        let task = tokio::spawn(async move {
            axum::serve(listener, router(state).into_make_service_with_connect_info::<std::net::SocketAddr>()).await
        });
        println!("http://{address}");
        loop {
            match input.next_line().await?.as_deref() {
                Some("fail") => store.set(FaultPlan { p_err_before: 1.0,
                    only_keys: Some(vec!["manifest.pb".into()]), ..Default::default() }),
                Some("lost") => store.set(FaultPlan { p_err_after: 1.0,
                    only_keys: Some(vec!["manifest.pb".into()]), ..Default::default() }),
                Some("heal") => store.heal(),
                Some("restart") => { store.heal(); task.abort(); task.await.ok(); break; }
                _ => { task.abort(); return Ok(()); }
            }
            println!("ready");
        }
        std::fs::remove_dir_all(root.join(format!("cache-{generation}")))?;
        generation += 1;
    }
}
