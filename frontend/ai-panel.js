/* proxmox-ai — chat panel. Injected into pvemanagerlib.js by patch.sh.
 * Adds an "AI" button to the top toolbar; opens a chat window that talks to
 * the proxmox-ai backend over SSE, forwarding the user's PVE session cookie.
 */
var PVE_AI_BACKEND = window.PVE_AI_BACKEND || 'http://127.0.0.1:9000';

Ext.define('PVE.ai.Panel', {
    extend: 'Ext.window.Window',
    alias: 'widget.pveAiPanel',

    title: 'AI Assistant',
    width: 520,
    height: 640,
    layout: 'fit',
    closable: true,
    closeAction: 'hide',
    maximizable: true,

    backendUrl: PVE_AI_BACKEND, // proxied to backend by patch (see patch.sh notes)

    // in-flight fetch abort controllers — cancelled on close so a stuck
    // stream can't wedge the window
    _abortControllers: [],

    // The framework calls onClose automatically when the window closes (X,
    // Esc, or programmatic close). Just do cleanup here; do NOT override
    // close() too — overriding both re-runs cleanup and can wedge the hide.
    onClose: function() {
        this._abortControllers.forEach(function(c) { try { c.abort(); } catch (e) {} });
        this._abortControllers = [];
    },

    initComponent: function() {
        var me = this;

        // active conversation; set from the 'session' SSE event on first send,
        // or immediately when a conversation is picked from History
        me.sessionId = null;

        me.messageStore = Ext.create('Ext.data.Store', {
            fields: ['role', 'text', 'kind', 'execId', 'status'],
            data: [],
        });

        // Plain menu; items are built imperatively from /sessions each time it opens.
        me.historyMenu = Ext.create('Ext.menu.Menu', {
            cls: 'pve-ai-history',
            plain: true,
            listeners: {
                beforeshow: function() { me.loadSessions(); },
            },
        });
        // Delete (✕) clicks: the menu element doesn't exist until first render,
        // so bind a delegated handler once it's rendered.
        me.historyMenu.on('afterrender', function(menu) {
            menu.getEl().on('click', function(e) {
                var del = e.getTarget('.pve-ai-hist-del');
                if (del) {
                    e.preventDefault();
                    e.stopEvent();
                    me.deleteSession(del.getAttribute('data-id'));
                    return false;
                }
            });
        });

        me.messagesView = Ext.create('Ext.view.View', {
            store: me.messageStore,
            autoScroll: true,
            cls: 'pve-ai-messages',
            itemSelector: 'div.pve-ai-msg',
            tpl: new Ext.XTemplate(
                '<tpl for=".">',
                '<div class="pve-ai-msg pve-ai-{role}">',
                '<div class="pve-ai-bubble">{text}</div>',
                '<tpl if="kind === \'exec\'">',
                '<div class="pve-ai-exec" data-exec-id="{execId}">',
                '<span class="pve-ai-exec-status">{status}</span>',
                '<tpl if="status === \'pending\'">',
                '<button class="pve-ai-approve" data-id="{execId}">Approve</button>',
                '<button class="pve-ai-deny" data-id="{execId}">Deny</button>',
                '</tpl>',
                '</div>',
                '</tpl>',
                '</div>',
                '</tpl>'
            ),
            listeners: {
                itemclick: function(view, record, item, idx, e) {
                    var t = e.getTarget('button');
                    if (!t) { return; }
                    var id = t.getAttribute('data-id');
                    if (t.classList.contains('pve-ai-approve')) {
                        me.execAction(id, 'approve');
                    } else if (t.classList.contains('pve-ai-deny')) {
                        me.execAction(id, 'deny');
                    }
                },
            },
        });

        me.input = Ext.create('Ext.form.field.TextArea', {
            flex: 1,
            emptyText: 'Ask about your cluster... (Shift+Enter for newline)',
            enableKeyEvents: true,
            listeners: {
                specialkey: function(field, e) {
                    if (e.getKey() === e.ENTER && !e.shiftKey) {
                        e.preventDefault();
                        me.send();
                    }
                },
            },
        });

        me.items = [{
            xtype: 'panel',
            layout: { type: 'vbox', align: 'stretch' },
            defaultType: 'panel',
            items: [
                Ext.apply(me.messagesView, { flex: 1 }),
                {
                    xtype: 'toolbar',
                    items: [
                        me.input,
                        {
                            xtype: 'button',
                            text: 'Send',
                            margin: '0 0 0 6',
                            handler: function() { me.send(); },
                        },
                        {
                            xtype: 'button',
                            text: 'New',
                            margin: '0 0 0 6',
                            handler: function() { me.newSession(); },
                        },
                        {
                            xtype: 'button',
                            text: 'History',
                            margin: '0 0 0 6',
                            menu: me.historyMenu,
                        },
                        {
                            xtype: 'button',
                            text: 'Settings',
                            margin: '0 0 0 6',
                            handler: function() {
                                Ext.create('PVE.ai.Settings', { backendUrl: me.backendUrl }).show();
                            },
                        },
                    ],
                },
            ],
        }];

        me.callParent();
    },

    authHeaders: function() {
        var cookie = Ext.util.Cookies.get('PVEAuthCookie') || '';
        return {
            'PVE-Auth-Cookie': cookie,
            'CSRFPreventionToken': (typeof Proxmox !== 'undefined' && Proxmox.CSRFPreventionToken) || '',
            'Content-Type': 'application/json',
        };
    },

    addMessage: function(role, text, extra) {
        var rec = Ext.apply({ role: role, text: text, kind: 'chat', execId: '', status: '' }, extra || {});
        var added = this.messageStore.add(rec);
        var record = Ext.isArray(added) ? added[0] : added;
        var el = this.messagesView.getEl();
        if (el) { el.scroll('b', 100000); }
        return record;
    },

    // ----- conversation history -----

    newSession: function() {
        // dropping sessionId means the next /chat creates a fresh session server-side
        this.sessionId = null;
        this.messageStore.removeAll();
        this.input.setValue('');
        this.input.focus();
    },

    loadSessions: function() {
        var me = this;
        fetch(me.backendUrl + '/sessions', { headers: me.authHeaders() })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var sessions = data.sessions || [];
                me.historyMenu.removeAll();
                if (!sessions.length) {
                    me.historyMenu.add({ text: '(no past conversations)', disabled: true });
                    return;
                }
                sessions.forEach(function(s) {
                    var when = me.fmtTime(s.updated);
                    me.historyMenu.add({
                        text: Ext.String.htmlEncode(s.title || s.id) +
                              ' <span class="pve-ai-hist-time">' + when + '</span>' +
                              ' <span class="pve-ai-hist-del" data-id="' + s.id + '" title="Delete">\u00d7</span>',
                        sessionId: s.id,
                        handler: function() { me.loadSession(s.id); },
                    });
                });
                me.historyMenu.add('-');
                me.historyMenu.add({
                    text: 'Clear all history',
                    cls: 'pve-ai-hist-clear',
                    handler: function() { me.clearAllSessions(); },
                });
            })
            .catch(function() { /* keep previous items on error */ });
    },

    fmtTime: function(iso) {
        var d = new Date(iso);
        return isNaN(d) ? '' : d.toLocaleDateString() + ' ' +
            d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    },

    loadSession: function(sid) {
        var me = this;
        fetch(me.backendUrl + '/sessions/' + sid, { headers: me.authHeaders() })
            .then(function(r) {
                if (!r.ok) { throw new Error('HTTP ' + r.status); }
                return r.json();
            })
            .then(function(s) {
                me.sessionId = s.id;
                me.messageStore.removeAll();
                (s.messages || []).forEach(function(m) {
                    if (m.role === 'user' || m.role === 'assistant') {
                        me.addMessage(m.role, m.content);
                    }
                });
                me.setTitle('AI Assistant — ' + (s.title || s.id));
            })
            .catch(function(err) {
                Ext.Msg.alert('History', 'Failed to load conversation: ' + err);
            });
    },

    deleteSession: function(sid) {
        var me = this;
        fetch(me.backendUrl + '/sessions/' + sid, {
            method: 'DELETE',
            headers: me.authHeaders(),
        }).then(function() {
            // rebuild the menu contents and reset if we deleted the active one
            me.loadSessions();
            if (me.sessionId === sid) { me.newSession(); }
        });
    },

    clearAllSessions: function() {
        var me = this;
        Ext.Msg.confirm('Clear History', 'Delete ALL past conversations? This cannot be undone.', function(btn) {
            if (btn !== 'yes') { return; }
            fetch(me.backendUrl + '/sessions', {
                method: 'DELETE',
                headers: me.authHeaders(),
            }).then(function() {
                me.loadSessions();
                me.newSession();
            });
        });
    },

    send: function() {
        var me = this;
        var text = me.input.getValue().trim();
        if (!text) { return; }
        me.input.setValue('');
        me.addMessage('user', text);
        var assistantRec = me.addMessage('assistant', '…');

        var controller = new AbortController();
        me._abortControllers.push(controller);

        fetch(me.backendUrl + '/chat', {
            method: 'POST',
            headers: me.authHeaders(),
            body: JSON.stringify({ message: text, session_id: me.sessionId || null }),
            signal: controller.signal,
        }).then(function(resp) {
            if (!resp.ok) {
                assistantRec.set('text', 'Error: HTTP ' + resp.status);
                assistantRec.commit();
                return;
            }
            var reader = resp.body.getReader();
            var decoder = new TextDecoder();
            var buf = '';
            function pump() {
                reader.read().then(function(r) {
                    if (r.done) { return; }
                    buf += decoder.decode(r.value, { stream: true });
                    var events = buf.split(/\r?\n\r?\n/);
                    buf = events.pop();
                    events.forEach(function(ev) { me.handleSSE(ev, assistantRec); });
                    pump();
                });
            }
            pump();
        }).catch(function(err) {
            if (err && err.name === 'AbortError') { return; } // closed by user
            assistantRec.set('text', 'Connection error: ' + err);
            assistantRec.commit();
        });
    },

    handleSSE: function(raw, assistantRec) {
        var me = this;
        var event = 'message', data = '';
        raw.split(/\r?\n/).forEach(function(line) {
            line = line.replace(/\r$/, '');
            if (line.indexOf('event:') === 0) { event = line.slice(6).trim(); }
            if (line.indexOf('data:') === 0) { data += line.slice(5).trim(); }
        });
        if (!data) { return; }
        var payload = {};
        try { payload = JSON.parse(data); } catch (e) { return; }

        if (event === 'session') {
            me.sessionId = payload.session_id;
            // keep the History list current once a new conversation exists
            me.loadSessions();
        } else if (event === 'delta') {
            var cur = assistantRec.get('text');
            assistantRec.set('text', (cur === '…' ? '' : cur) + payload.text);
            assistantRec.commit();
            me.messagesView.refresh();
            var el = me.messagesView.getEl();
            if (el) { el.scroll('b', 100000); }
        } else if (event === 'exec_request') {
            me.addMessage('assistant',
                'Proposed command on ' + payload.node + ' [' + payload.category + ']: ' + payload.command +
                '\n(' + payload.reason + ')',
                { kind: 'exec', execId: payload.id, status: payload.status });
        } else if (event === 'exec_result') {
            me.addMessage('assistant',
                'Ran: ' + payload.command + '\nexit ' + payload.exit_code + '\n' + (payload.output || ''),
                { kind: 'exec', execId: payload.id, status: payload.status });
        } else if (event === 'task_progress') {
            var txt = payload.state === 'stopped'
                ? 'Task ' + payload.upid + ' finished: ' + payload.exitstatus
                : 'Task ' + payload.upid + ': ' + payload.state +
                  (payload.progress != null ? ' (' + Math.round(payload.progress * 100) + '%)' : '');
            me.addMessage('assistant', txt, { kind: 'task', execId: '', status: payload.state });
        } else if (event === 'error') {
            assistantRec.set('text', 'Error: ' + payload.error);
            assistantRec.commit();
        }
    },

    execAction: function(id, action) {
        var me = this;
        fetch(me.backendUrl + '/exec/' + id + '/' + action, {
            method: 'POST',
            headers: me.authHeaders(),
        }).then(function(resp) {
            return resp.json().then(function(body) { return { ok: resp.ok, body: body }; });
        }).then(function(r) {
            var result = r.body || {};
            if (!r.ok) {
                me.addMessage('assistant',
                    (action === 'approve' ? 'Approve' : 'Deny') + ' failed: ' +
                    (result.detail || ('HTTP ' + r.status)),
                    { kind: 'exec', execId: id, status: 'failed' });
                return;
            }
            var exitTxt = (result.exit_code === null || result.exit_code === undefined)
                ? '' : ' exit ' + result.exit_code;
            me.addMessage('assistant',
                action === 'approve'
                    ? ('Approved.' + exitTxt + (result.output ? '\n' + result.output : ''))
                    : 'Denied.',
                { kind: 'exec', execId: id, status: result.status || 'done' });
        }).catch(function(err) {
            me.addMessage('assistant', 'Action error: ' + err,
                { kind: 'exec', execId: id, status: 'failed' });
        });
    },
});

/* Toolbar button bootstrap — called from the injected snippet in pvemanagerlib.js */
(function injectCss() {
    var css = ''
        + '.pve-ai-messages{padding:8px;background:#1b1e23;overflow-y:auto;}'
        + '.pve-ai-msg{margin:6px 0;clear:both;}'
        + '.pve-ai-bubble{display:inline-block;max-width:85%;padding:8px 10px;'
        + 'border-radius:8px;white-space:pre-wrap;word-wrap:break-word;color:#e8e8e8;}'
        + '.pve-ai-user{text-align:right;}'
        + '.pve-ai-user .pve-ai-bubble{background:#2f6f9f;}'
        + '.pve-ai-assistant .pve-ai-bubble{background:#2b3038;}'
        + '.pve-ai-exec{margin-top:6px;font-family:monospace;}'
        + '.pve-ai-task .pve-ai-bubble{background:#26343f;border-left:3px solid #2f6f9f;'
        + 'font-family:monospace;font-size:12px;}'
        + '.pve-ai-approve,.pve-ai-deny{margin-right:6px;padding:3px 10px;cursor:pointer;}'
        + '.pve-ai-approve{background:#2e7d32;color:#fff;border:0;border-radius:4px;}'
        + '.pve-ai-deny{background:#c62828;color:#fff;border:0;border-radius:4px;}'
        + '.pve-ai-history .x-menu-item-link{max-width:380px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
        + '.pve-ai-hist-time{color:#8a8f98;font-size:11px;}'
        + '.pve-ai-hist-del{margin-left:8px;color:#8a8f98;cursor:pointer;padding:0 4px;}'
        + '.pve-ai-hist-del:hover{color:#e57373;}';
    var style = document.createElement('style');
    style.type = 'text/css';
    style.appendChild(document.createTextNode(css));
    document.head.appendChild(style);
})();

function pveAiOpenPanel() {
    try {
        var win = Ext.WindowManager.get('pve-ai-panel');
        if (!win) {
            win = Ext.create('PVE.ai.Panel');
            win.id = 'pve-ai-panel';
        }
        win.show();
    } catch (e) {
        // surface the real error instead of silently doing nothing
        if (window.Ext && Ext.Msg) {
            Ext.Msg.alert('AI panel error', (e && e.message) ? e.message : String(e));
        } else {
            alert('AI panel error: ' + (e && e.message ? e.message : e));
        }
        if (window.console && console.error) { console.error(e); }
    }
}

function pveAiEnsureButton() {
    // the header panel holds Documentation / Create VM / Create CT / userinfo
    var userBtn = Ext.ComponentQuery.query('#userinfo')[0];
    var header = userBtn && userBtn.up('panel');
    if (!header) { return false; }
    // already present?
    var existing = header.items.items.filter(function(i) { return i.itemId === 'pveAiButton'; })[0];
    if (existing) { return true; }
    // insert just before the user menu button
    var idx = header.items.items.indexOf(userBtn);
    header.insert(idx < 0 ? header.items.length : idx, {
        xtype: 'button',
        itemId: 'pveAiButton',
        text: 'AI',
        iconCls: 'fa fa-comment',
        handler: pveAiOpenPanel,
    });
    header.updateLayout();
    return true;
}

Ext.onReady(function() {
    // initial insert (retry until the header is rendered)
    var tries = 0;
    var timer = setInterval(function() {
        if (pveAiEnsureButton() || ++tries > 40) { clearInterval(timer); }
    }, 250);

    // re-add after every navigation / view change (Proxmox re-renders the header)
    if (window.location.hash !== undefined) {
        var lastHash = window.location.hash;
        setInterval(function() {
            if (window.location.hash !== lastHash) {
                lastHash = window.location.hash;
                Ext.Function.defer(pveAiEnsureButton, 300);
            }
        }, 300);
    }
    // safety net: also re-check periodically in case the header re-renders without a hash change
    setInterval(pveAiEnsureButton, 2000);
});
