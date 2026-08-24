/** A bare IPv4 host, which a DHCP lease can move without warning. */
const BARE_IP = /^https?:\/\/\d{1,3}(\.\d{1,3}){3}(:\d+)?$/;

/**
 * Where Slack digest links point.
 *
 * Worth showing because it fails silently: if this base is wrong, every link
 * already sent is dead and nothing else on screen would say so. It happened
 * during development — the host's address moved from 192.168.1.5 to
 * 192.168.0.133 between one afternoon and the next, a different subnet.
 */
export function LinkBase({ url }: { url: string }) {
  const fragile = BARE_IP.test(url);
  const local = /localhost|127\.0\.0\.1/.test(url);
  return (
    <div className="linkbase">
      <h3>Slack links point to</h3>
      <p>
        <span className="mono">{url}</span>
      </p>
      {fragile ? (
        <p className="linkbase__warn">
          That is a bare IP address, which your router can reassign — every link already sent would
          then be dead. A hostname such as <span className="mono">machine-name.local</span>, or a
          fixed address from IT, survives the change.
        </p>
      ) : local ? (
        <p className="linkbase__warn">
          Only this machine can open a <span className="mono">localhost</span> link. Colleagues
          clicking a Slack digest will get nothing.
        </p>
      ) : null}
    </div>
  );
}
