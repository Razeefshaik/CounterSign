// C51: WebAuthn uses the dashboard origin only; tokens/assertions are never stored.
export const passkeySupported = (scope = globalThis) =>
  Boolean(scope.PublicKeyCredential && scope.navigator?.credentials?.create && scope.navigator?.credentials?.get);

export function fromBase64url(value) {
  const encoded = value.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
}
export function toBase64url(value) {
  return btoa(String.fromCharCode(...new Uint8Array(value)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
export function decodeOptions(options) {
  const result = {...options, challenge: fromBase64url(options.challenge)};
  if (options.user) result.user = {...options.user, id: fromBase64url(options.user.id)};
  for (const key of ["allowCredentials", "excludeCredentials"]) {
    if (options[key]) result[key] = options[key].map(c => ({...c, id: fromBase64url(c.id)}));
  }
  return result;
}
export function encodeCredential(credential) {
  if (!credential) throw new Error("PASSKEY_INVALID");
  const response = {};
  for (const name of ["clientDataJSON", "attestationObject", "authenticatorData", "signature", "userHandle"]) {
    if (credential.response[name] != null) response[name] = toBase64url(credential.response[name]);
  }
  if (credential.response.getTransports) response.transports = credential.response.getTransports();
  return {id: credential.id, rawId: toBase64url(credential.rawId), type: credential.type, response};
}

// C61: newDevice registers this device's own passkey even when another one is already registered.
export async function countersign(proposal, {token = "", fetcher = fetch, scope = globalThis,
  isCurrent = () => true, onSetup = () => {}, newDevice = false} = {}) {
  if (!passkeySupported(scope)) throw new Error("PASSKEY_UNAVAILABLE");
  const payload_hash = proposal.approval_request?.payload_hash;
  const base = `/api/v1/owner/proposals/${encodeURIComponent(proposal.id)}/passkey`;
  async function post(path, body = {}) {
    if (!isCurrent()) throw new Error("PASSKEY_VIEW_CHANGED");
    const headers = {"Content-Type": "application/json"};
    if (token) headers["X-Owner-Token"] = token;
    const response = await fetcher(path, {method: "POST", headers, body: JSON.stringify(body)});
    if (!response.ok) {
      // Raw server bodies and authenticator errors never appear in the UI.
      const error = new Error("PASSKEY_REQUEST_FAILED"); error.status = response.status; throw error;
    }
    return response.json();
  }
  let options = await post(base + "/options", {payload_hash});
  if (!options.registered || newDevice) {
    onSetup();
    const registration = await post("/api/v1/owner/passkeys/register/options");
    if (!isCurrent()) throw new Error("PASSKEY_VIEW_CHANGED");
    let credential = null;
    try {
      credential = await scope.navigator.credentials.create({publicKey: decodeOptions(registration.publicKey)});
    } catch (error) {
      // This device already holds a registered passkey (excluded); sign with it instead.
      if (!(newDevice && error?.name === "InvalidStateError")) throw error;
    }
    if (credential) {
      await post("/api/v1/owner/passkeys/register/verify", {
        challenge_id: registration.challenge_id, credential: encodeCredential(credential),
      });
    }
    options = await post(base + "/options", {payload_hash});
  }
  if (!isCurrent()) throw new Error("PASSKEY_VIEW_CHANGED");
  const credential = await scope.navigator.credentials.get({publicKey: decodeOptions(options.publicKey)});
  return post(base + "/approve", {challenge_id: options.challenge_id, payload_hash,
    credential: encodeCredential(credential)});
}
