import { useState } from "react";
import * as DM from "@radix-ui/react-dropdown-menu";
import { CircleUser, LogOut } from "lucide-react";
import { errorMessage } from "../api";
import { useAuth } from "../auth";

/**
 * Account button: a Radix dropdown menu with who is signed in and Log out. The menu is portalled to
 * <body>, so neither the sidebar's stacking context nor its scrolling can cover or clip it, and Radix
 * handles focus, Escape, outside clicks and flipping near the screen edges.
 * `placement` says where the button lives: the sidebar foot (opens upwards) or the phone header.
 */
export function UserMenu({ placement = "sidebar" }: { placement?: "sidebar" | "header" }) {
  const auth = useAuth();
  const user = auth.status.kind === "in" ? auth.status.user : null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  if (!user) return null;

  const logOut = async () => {
    setBusy(true);
    setError(null);
    try {
      await auth.signOut();
    } catch (err) {
      setError(`Not logged out: ${errorMessage(err)}. You are still signed in; try again.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <DM.Root
      // Not modal: while a logout is pending on a slow network the menu stays open, but the rest
      // of the app stays usable (a modal menu would lock the whole page until the request ends).
      modal={false}
      open={open}
      onOpenChange={(next) => {
        if (!next && busy) return; // keep it open until the logout answer is shown
        setOpen(next);
      }}
    >
      <DM.Trigger asChild>
        <button type="button" className="icon-btn" aria-label={`Account (signed in as ${user})`} title={`Signed in as ${user}`}>
          <CircleUser />
        </button>
      </DM.Trigger>
      <DM.Portal>
        <DM.Content
          className="glass menu-pop"
          side={placement === "sidebar" ? "top" : "bottom"}
          align={placement === "sidebar" ? "start" : "end"}
          sideOffset={8}
          collisionPadding={8}
        >
          <DM.Label className="menu-who">
            <span>Signed in as</span>
            <b>{user}</b>
          </DM.Label>
          <DM.Separator className="menu-sep" />
          <DM.Item
            className="menu-item"
            disabled={busy}
            onSelect={(e) => {
              e.preventDefault(); // keep the menu open: "Logging out…" and any failure stay visible
              void logOut();
            }}
          >
            {busy ? <span className="spinner" aria-hidden="true" /> : <LogOut aria-hidden="true" />}
            {busy ? "Logging out…" : "Log out"}
          </DM.Item>
          {error && (
            <p className="menu-err" role="alert">
              {error}
            </p>
          )}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}
