//! Negotiate before any command is accepted. Product versions are diagnostic only.
use gitpickle_application::domain::ErrorCode;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct VersionRange {
    pub minimum: u16,
    pub maximum: u16,
}
impl VersionRange {
    fn common(self, other: Self) -> Result<u16, ErrorCode> {
        if self.minimum == 0
            || other.minimum == 0
            || self.minimum > self.maximum
            || other.minimum > other.maximum
        {
            return Err(ErrorCode::InvalidInput);
        }
        let maximum = self.maximum.min(other.maximum);
        if maximum < self.minimum.max(other.minimum) {
            return Err(ErrorCode::UnsupportedVersion);
        }
        Ok(maximum)
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Hello {
    pub software_version: String,
    pub management: VersionRange,
    pub machine_result: VersionRange,
    pub capabilities: Vec<String>,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Negotiated {
    pub management: u16,
    pub machine_result: u16,
}
pub fn negotiate(
    client: &Hello,
    server: &Hello,
    required: &[&str],
) -> Result<Negotiated, ErrorCode> {
    let management = client.management.common(server.management)?;
    let machine_result = client.machine_result.common(server.machine_result)?;
    if required
        .iter()
        .any(|required| !server.capabilities.iter().any(|c| c == required))
    {
        return Err(ErrorCode::UnsupportedCapability);
    }
    Ok(Negotiated {
        management,
        machine_result,
    })
}
