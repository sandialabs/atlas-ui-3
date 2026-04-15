/**
 * Encode a multi-segment file key for use in URL paths.
 * Encodes each path segment individually (e.g. @ -> %40) while
 * preserving `/` as the path separator, unlike encodeURIComponent
 * which also encodes `/` to `%2F` and can break proxy routing.
 */
const encodeFileKeyPath = (key) =>
  key.split('/').map(encodeURIComponent).join('/')

export default encodeFileKeyPath
