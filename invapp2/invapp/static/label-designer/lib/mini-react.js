const Fragment = Symbol('MiniReact.Fragment');

const roots = new Map();
const componentStore = new Map();
let currentRootId = null;
let currentComponentPath = null;
let hookCursor = 0;
let activeComponentPaths = new Set();
let effectsToRun = [];
let isRendering = false;
const pendingRenders = new Set();

function composePath(parentPath, key) {
  if (parentPath === null || parentPath === undefined || parentPath === "") {
    return String(key);
  }
  return `${parentPath}.${key}`;
}

function flatten(children) {
  const result = [];
  const stack = [...children];
  while (stack.length) {
    const child = stack.shift();
    if (Array.isArray(child)) {
      stack.unshift(...child);
    } else if (child === false || child === true || child === null || child === undefined) {
      continue;
    } else {
      result.push(child);
    }
  }
  return result;
}

function createElement(type, props, ...children) {
  const finalProps = props ? { ...props } : {};
  const key = finalProps.key !== undefined ? finalProps.key : null;
  if (key !== null) {
    delete finalProps.key;
  }
  const flatChildren = flatten(children);
  if (finalProps.children === undefined && flatChildren.length > 0) {
    finalProps.children = flatChildren;
  }
  return {
    type,
    props: finalProps,
    key,
    children: flatChildren,
  };
}

function createRoot(container) {
  if (!container) {
    throw new Error("A container element is required to create a root.");
  }
  const rootId = Symbol("mini-react-root");
  const rootRecord = { id: rootId, container, element: null };
  roots.set(rootId, rootRecord);
  return {
    render(element) {
      rootRecord.element = element;
      scheduleRender(rootId);
    },
  };
}

function render(element, container) {
  return createRoot(container).render(element);
}

function scheduleRender(rootId) {
  if (!roots.has(rootId)) {
    return;
  }
  pendingRenders.add(rootId);
  if (isRendering) {
    return;
  }
  enqueue(processRenderQueue);
}

function processRenderQueue() {
  if (isRendering) {
    return;
  }
  isRendering = true;
  try {
    for (const rootId of Array.from(pendingRenders)) {
      pendingRenders.delete(rootId);
      const rootRecord = roots.get(rootId);
      if (!rootRecord) {
        continue;
      }
      performRender(rootRecord);
    }
  } finally {
    isRendering = false;
  }
}

function enqueue(task) {
  if (typeof queueMicrotask === "function") {
    queueMicrotask(task);
  } else {
    Promise.resolve().then(task);
  }
}

function performRender(rootRecord) {
  currentRootId = rootRecord.id;
  activeComponentPaths = new Set();
  effectsToRun = [];
  const { element, container } = rootRecord;
  const dom = renderVNode(element, "root");
  container.innerHTML = "";
  if (dom) {
    container.appendChild(dom);
  }
  cleanupUnmountedComponents();
  flushEffects();
  currentRootId = null;
}

function renderVNode(vnode, path) {
  if (vnode === null || vnode === undefined || typeof vnode === "boolean") {
    return document.createComment("mini-react-empty");
  }
  if (typeof vnode === "string" || typeof vnode === "number") {
    return document.createTextNode(String(vnode));
  }
  if (Array.isArray(vnode)) {
    const fragment = document.createDocumentFragment();
    vnode.forEach((child, index) => {
      const childNode = renderVNode(child, composePath(path, index));
      if (childNode) {
        fragment.appendChild(childNode);
      }
    });
    return fragment;
  }
  const { type, props = {}, children = [], key } = vnode;
  if (type === Fragment) {
    const fragment = document.createDocumentFragment();
    children.forEach((child, index) => {
      const childNode = renderVNode(child, composePath(path, key ?? index));
      if (childNode) {
        fragment.appendChild(childNode);
      }
    });
    return fragment;
  }
  if (typeof type === "function") {
    return renderComponent(vnode, path);
  }
  if (typeof type !== "string") {
    throw new Error(`Unsupported vnode type: ${type}`);
  }
  const element = document.createElement(type);
  applyProps(element, props);
  children.forEach((child, index) => {
    const childNode = renderVNode(child, composePath(path, child && child.key != null ? `k${child.key}` : index));
    if (childNode) {
      element.appendChild(childNode);
    }
  });
  return element;
}

function renderComponent(vnode, path) {
  const { type: Component, props = {} } = vnode;
  enterComponent(path);
  let rendered;
  try {
    rendered = Component({ ...props, children: props.children ?? vnode.children ?? [] });
  } finally {
    exitComponent();
  }
  return renderVNode(rendered, composePath(path, "child"));
}

function enterComponent(path) {
  currentComponentPath = path;
  hookCursor = 0;
  activeComponentPaths.add(path);
  if (!componentStore.has(path)) {
    componentStore.set(path, { hooks: [], rootId: currentRootId });
  }
  const store = componentStore.get(path);
  store.rootId = currentRootId;
}

function exitComponent() {
  currentComponentPath = null;
}

function getHook(expectedType, initializer) {
  if (currentComponentPath === null) {
    throw new Error("Hooks can only be used within a component body.");
  }
  const store = componentStore.get(currentComponentPath);
  const hooks = store.hooks;
  if (!hooks[hookCursor]) {
    const value = typeof initializer === "function" ? initializer() : initializer;
    hooks[hookCursor] = {
      type: expectedType,
      value,
      deps: undefined,
      cleanup: undefined,
      rootId: store.rootId,
    };
  }
  const hook = hooks[hookCursor];
  if (hook.type !== expectedType) {
    throw new Error("Hook order has changed between renders.");
  }
  hook.rootId = store.rootId;
  return hook;
}

function useState(initialValue) {
  const hook = getHook("state", initialValue);
  const setState = (nextValue) => {
    const value = typeof nextValue === "function" ? nextValue(hook.value) : nextValue;
    if (Object.is(value, hook.value)) {
      return;
    }
    hook.value = value;
    scheduleRender(hook.rootId);
  };
  const value = hook.value;
  hookCursor += 1;
  return [value, setState];
}

function useRef(initialValue) {
  const hook = getHook("ref", () => ({ current: initialValue }));
  if (hook.value === undefined) {
    hook.value = { current: initialValue };
  }
  hookCursor += 1;
  return hook.value;
}

function depsChanged(prevDeps, nextDeps) {
  if (prevDeps === undefined || nextDeps === undefined) {
    return true;
  }
  if (prevDeps === null || nextDeps === null) {
    return true;
  }
  if (prevDeps.length !== nextDeps.length) {
    return true;
  }
  for (let i = 0; i < prevDeps.length; i += 1) {
    if (!Object.is(prevDeps[i], nextDeps[i])) {
      return true;
    }
  }
  return false;
}

function useMemo(factory, deps) {
  const hook = getHook("memo", () => ({ value: factory(), deps }));
  if (hook.value === undefined || depsChanged(hook.value.deps, deps)) {
    hook.value = { value: factory(), deps: deps ? [...deps] : deps };
  }
  hookCursor += 1;
  return hook.value.value;
}

function useCallback(callback, deps) {
  return useMemo(() => callback, deps);
}

function useEffect(effect, deps) {
  const hook = getHook("effect", () => ({ deps: deps ? [...deps] : deps, cleanup: undefined }));
  const shouldRun = depsChanged(hook.deps, deps);
  if (shouldRun) {
    effectsToRun.push(() => {
      if (typeof hook.cleanup === "function") {
        try {
          hook.cleanup();
        } catch (cleanupError) {
          console.error(cleanupError);
        }
      }
      hook.deps = deps ? [...deps] : deps;
      hook.cleanup = effect() || undefined;
    });
  }
  hookCursor += 1;
}

function flushEffects() {
  const pending = effectsToRun;
  effectsToRun = [];
  pending.forEach((run) => run());
}

function cleanupUnmountedComponents() {
  for (const [path, store] of componentStore.entries()) {
    if (!activeComponentPaths.has(path)) {
      store.hooks.forEach((hook) => {
        if (hook && hook.type === "effect" && typeof hook.cleanup === "function") {
          try {
            hook.cleanup();
          } catch (cleanupError) {
            console.error(cleanupError);
          }
        }
      });
      componentStore.delete(path);
    }
  }
}

function applyProps(element, props) {
  for (const [key, value] of Object.entries(props)) {
    if (key === "children" || key === "ref") {
      continue;
    }
    if (key === "class" || key === "className") {
      element.className = value ?? "";
      continue;
    }
    if (key === "style") {
      if (typeof value === "string") {
        element.setAttribute("style", value);
      } else if (value && typeof value === "object") {
        for (const [styleKey, styleValue] of Object.entries(value)) {
          element.style[styleKey] = styleValue;
        }
      }
      continue;
    }
    if (key.startsWith("on") && typeof value === "function") {
      const eventName = key.slice(2).toLowerCase();
      element.addEventListener(eventName, value);
      continue;
    }
    if (value === false || value === null || value === undefined) {
      continue;
    }
    if (value === true) {
      element.setAttribute(key, "");
      continue;
    }
    if (key in element) {
      element[key] = value;
    } else {
      element.setAttribute(key, value);
    }
  }
  if (props.ref) {
    const ref = props.ref;
    if (typeof ref === "function") {
      ref(element);
    } else if (typeof ref === "object") {
      ref.current = element;
    }
  }
}

export {
  Fragment,
  createElement,
  createRoot,
  render,
  useState,
  useEffect,
  useMemo,
  useCallback,
  useRef,
};

const MiniReact = {
  Fragment,
  createElement,
  createRoot,
  render,
  useState,
  useEffect,
  useMemo,
  useCallback,
  useRef,
};

export default MiniReact;
