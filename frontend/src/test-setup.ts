/**
 * Tells React it is running inside a test that drives `act()`.
 *
 * Without this flag React logs "The current testing environment is not
 * configured to support act(...)" on every render and, more importantly, does
 * not guarantee that effects and state updates are flushed before `act()`
 * returns — so an assertion can read the DOM one render behind and pass or fail
 * depending on timing.
 */
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

export {};
