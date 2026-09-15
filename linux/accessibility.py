"""AT-SPI2 via GNOME's supported GObject-introspection bindings."""
import time
import gi
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi, GLib
from core import References, fail


class Accessibility:
    def __init__(self):
        Atspi.set_timeout(500, 1500)
        self.references = References()

    def available(self):
        try:
            return Atspi.get_desktop(0) is not None
        except GLib.Error:
            return False

    def app(self, pid):
        root = Atspi.get_desktop(0)
        for i in range(root.get_child_count()):
            child = root.get_child_at_index(i)
            if child and child.get_process_id() == pid:
                return child
        fail('ACCESSIBILITY_UNAVAILABLE', 'This app has not exposed an AT-SPI tree. Enable desktop accessibility or use screenshots and observed coordinates.')

    @staticmethod
    def secure(element):
        return element.get_role() == Atspi.Role.PASSWORD_TEXT

    def inspect(self, app, args):
        root = self.app(app['pid'])
        maximum = min(max(args.get('maxNodes', 500), 1), 3000)
        depth_limit = min(max(args.get('maxDepth', 12), 1), 30)
        query = args.get('query', '').casefold()
        deadline, elements, refs, visited = time.monotonic() + 12, [], {}, set()
        truncated = False

        def walk(element, depth, parent=None, protected=False):
            nonlocal truncated
            if len(refs) >= maximum or time.monotonic() > deadline:
                truncated = True
                return
            if element is None or element in visited:
                return
            visited.add(element)
            try:
                states = element.get_state_set()
                if states.contains(Atspi.StateType.DEFUNCT):
                    return
                identifier = 'e' + str(len(refs) + 1)
                refs[identifier] = element
                row = dict(id=identifier, role=element.get_role_name(), depth=depth,
                           title=(element.get_name() or '')[:500],
                           description=(element.get_description() or '')[:500],
                           enabled=states.contains(Atspi.StateType.ENABLED))
                if parent:
                    row['parent'] = parent
                attrs = element.get_attributes() or {}
                accessible_id = element.get_accessible_id() if hasattr(element, 'get_accessible_id') else ''
                if accessible_id or attrs.get('id'):
                    row['identifier'] = accessible_id or attrs['id']
                protected = protected or self.secure(element)
                if protected:
                    row['value'] = '[secure field]'
                else:
                    text = element.get_text_iface()
                    if text:
                        # Accessible.get_text() is an older interface accessor;
                        # call the Text interface explicitly to avoid that name collision.
                        row['value'] = Atspi.Text.get_text(text, 0, min(text.get_character_count(), 2000))
                    elif element.get_value_iface():
                        row['value'] = element.get_value_iface().get_current_value()
                action = element.get_action_iface()
                if action:
                    row['actions'] = [action.get_action_name(i) for i in range(action.get_n_actions())]
                component = element.get_component_iface()
                if component:
                    r = component.get_extents(Atspi.CoordType.SCREEN)
                    if r.width > 0 and r.height > 0 and abs(r.x) < 100000 and abs(r.y) < 100000:
                        row['bounds'] = dict(x=r.x, y=r.y, width=r.width, height=r.height)
                if not query or any(query in str(value).casefold() for value in row.values()):
                    elements.append(row)
                count = element.get_child_count()
                if depth >= depth_limit:
                    truncated = truncated or count > 0
                else:
                    for i in range(count):
                        if len(refs) >= maximum or time.monotonic() > deadline:
                            truncated = True
                            break
                        walk(element.get_child_at_index(i), depth + 1, identifier, protected)
            except GLib.Error:
                truncated = True

        walk(root, 0)
        snapshot = self.references.add(dict(pid=app['pid'], refs=refs))
        return dict(app=app, snapshotId=snapshot, expiresInSeconds=120, elements=elements,
                    visited=len(refs), truncated=truncated)

    def element(self, args):
        snapshot = self.references.get(args.get('snapshotId'), 'UNKNOWN_ELEMENT')
        element = snapshot['refs'].get(args.get('elementId'))
        if element is None:
            fail('UNKNOWN_ELEMENT', 'Use snapshotId and elementId from inspect.')
        try:
            if element.get_state_set().contains(Atspi.StateType.DEFUNCT) or element.get_process_id() != snapshot['pid']:
                fail('STALE_ELEMENT', 'The accessible element is no longer available. Inspect again.')
            element.get_role()
        except GLib.Error:
            fail('STALE_ELEMENT', 'The accessible element is no longer available. Inspect again.')
        return element

    def click(self, args):
        element = self.element(args)
        action = element.get_action_iface()
        if not action:
            fail('ACTION_UNSUPPORTED', 'This element has no accessibility actions.')
        names = [action.get_action_name(i) for i in range(action.get_n_actions())]
        requested = args.get('action')
        if requested is None:
            requested = next((name for name in names if name.lower() in ('click', 'press', 'activate')), None)
        if requested not in names:
            fail('ACTION_UNSUPPORTED', 'Select an action listed in the observed element, or use screenshot coordinates.')
        if not action.do_action(names.index(requested)):
            fail('ACCESSIBILITY_ACTION_FAILED', 'The app rejected the accessibility action.')
        return dict(performed=requested, method='accessibility')

    def set_value(self, args):
        element = self.element(args)
        attribute, value = args.get('attribute', 'AXValue'), args.get('value')
        success = False
        if attribute == 'AXFocused' and value is True:
            component = element.get_component_iface()
            success = component is not None and component.grab_focus()
        elif attribute == 'AXSelected' and isinstance(value, bool):
            parent = element.get_parent()
            selection = parent.get_selection_iface() if parent else None
            if selection:
                index = element.get_index_in_parent()
                success = selection.select_child(index) if value else selection.deselect_child(index)
        elif attribute == 'AXValue':
            editable = element.get_editable_text_iface()
            numeric = element.get_value_iface()
            if editable and isinstance(value, str):
                success = editable.set_text_contents(value)
            elif numeric and isinstance(value, (int, float)) and not isinstance(value, bool):
                success = numeric.set_current_value(value)
        if not success:
            fail('NOT_SETTABLE', 'This element does not support that value or attribute.')
        return dict(performed='set_value', attribute=attribute)
