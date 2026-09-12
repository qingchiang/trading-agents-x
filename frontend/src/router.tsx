import {
  AnchorHTMLAttributes,
  MouseEvent,
  PropsWithChildren,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useRef,
} from "react";

type NavigateOptions = {
  replace?: boolean;
};

export type RouterLocation = {
  pathname: string;
  search: string;
  hash: string;
  key: string;
  sourceLibrary?: { url: string; key: string };
};

type RouterValue = {
  location: RouterLocation;
  navigate: (to: string, options?: NavigateOptions) => void;
};

const RouterContext = createContext<RouterValue | null>(null);

export function Router({
  children,
  initialPath,
}: PropsWithChildren<{ initialPath?: string }>) {
  const browserBacked = initialPath === undefined;
  const [location, setLocation] = useState<RouterLocation>(() => {
    const next = parseLocation(initialPath ?? browserLocation());
    return browserBacked ? { ...next, ...window.history.state?.researchNavigation, ...parseBrowserPath() } : next;
  });
  const locationRef = useRef(location);
  locationRef.current = location;

  useEffect(() => {
    if (!browserBacked) return;
    const previousRestoration = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    window.history.replaceState({ ...window.history.state, researchNavigation: locationRef.current }, "");
    const syncLocation = () => setLocation({ ...parseLocation(browserLocation()), ...window.history.state?.researchNavigation, ...parseBrowserPath() });
    window.addEventListener("popstate", syncLocation);
    return () => { window.history.scrollRestoration = previousRestoration; window.removeEventListener("popstate", syncLocation); };
  }, [browserBacked]);

  const navigate = useCallback(
    (to: string, options?: NavigateOptions) => {
      const current = locationRef.current;
      const next = parseLocation(to);
      if (options?.replace) next.key = current.key;
      if (next.pathname.startsWith("/timelines/")) {
        next.sourceLibrary = current.pathname === "/timelines"
          ? { url: locationPath(current), key: current.key }
          : current.sourceLibrary;
      }
      if (next.pathname === "/timelines" && locationPath(next) === current.sourceLibrary?.url) {
        const saved = sessionStorage.getItem(`tradingagents-position:${current.sourceLibrary.key}`);
        if (saved !== null) sessionStorage.setItem(`tradingagents-position:${next.key}`, saved);
      }
      if (browserBacked) {
        const method = options?.replace ? "replaceState" : "pushState";
        window.history[method]({ researchNavigation: next }, "", locationPath(next));
      }
      setLocation(next);
    },
    [browserBacked],
  );

  const value = useMemo(() => ({ location, navigate }), [location, navigate]);
  return (
    <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
  );
}

export function useHistoryEntryKey() {
  return useContext(RouterContext)?.location.key;
}

export function useNavigate() {
  return useRouter().navigate;
}

export function usePathname() {
  return useRouter().location.pathname;
}

export function useLocation() {
  return useRouter().location;
}

export function useParams() {
  const pathname = usePathname();
  const match = /^\/runs\/([^/]+)\/?$/.exec(pathname);
  return { runId: match ? decodeURIComponent(match[1]) : undefined };
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  to: string;
};

export function Link({ children, onClick, to, ...props }: LinkProps) {
  const navigate = useNavigate();
  const follow = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    navigate(to);
  };
  return (
    <a href={to} onClick={follow} {...props}>
      {children}
    </a>
  );
}

type NavLinkProps = Omit<LinkProps, "className"> & {
  className?: string | ((state: { isActive: boolean }) => string);
  end?: boolean;
};

export function NavLink({
  className,
  end = false,
  to,
  ...props
}: NavLinkProps) {
  const pathname = usePathname();
  const target = parseLocation(to).pathname;
  const isActive = end
    ? pathname === target
    : pathname === target || pathname.startsWith(`${target}/`);
  const resolvedClassName =
    typeof className === "function" ? className({ isActive }) : className;
  return <Link className={resolvedClassName} to={to} {...props} />;
}

function useRouter() {
  const context = useContext(RouterContext);
  if (!context) {
    throw new Error("Router components must be rendered inside Router");
  }
  return context;
}

function normalizePathname(pathname: string) {
  if (!pathname || pathname === "/") return "/";
  return `/${pathname.replace(/^\/+|\/+$/g, "")}`;
}

function parseLocation(path: string): RouterLocation {
  const url = new URL(path || "/", "http://tradingagents.local");
  return {
    pathname: normalizePathname(url.pathname),
    search: url.search,
    hash: url.hash,
    key: crypto.randomUUID(),
  };
}

function locationPath(location: RouterLocation) {
  return `${location.pathname}${location.search}${location.hash}`;
}

function browserLocation() {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function parseBrowserPath() {
  const { pathname, search, hash } = parseLocation(browserLocation());
  return { pathname, search, hash };
}
