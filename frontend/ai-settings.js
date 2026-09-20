/* proxmox-ai — settings window (root@pam only; backend enforces).
 * Master toggles, per-category permission matrix, SSH mode, rate limits,
 * kill switch, provider editor, audit viewer.
 */
Ext.define('PVE.ai.Settings', {
    extend: 'Ext.window.Window',
    alias: 'widget.pveAiSettings',

    title: 'AI Assistant Settings',
    width: 720,
    height: 560,
    layout: 'fit',
    modal: true,
    backendUrl: (typeof PVE_AI_BACKEND !== 'undefined') ? PVE_AI_BACKEND : 'http://127.0.0.1:9000',

    initComponent: function() {
        var me = this;

        me.securityPanel = Ext.create('Ext.form.Panel', {
            title: 'Security',
            bodyPadding: 10,
            autoScroll: true,
            items: [
                { xtype: 'checkbox', name: 'server_access', boxLabel: 'Grant server access to LLM (master switch)' },
                { xtype: 'checkbox', name: 'internet_access', boxLabel: 'Allow internet access (external provider URLs)' },
                { xtype: 'checkbox', name: 'online_providers', boxLabel: 'Allow online LLM providers (OpenAI, Anthropic, OpenRouter)' },
                { xtype: 'checkbox', name: 'guest_exec', boxLabel: 'Allow in-guest execution (qm agent / pct exec)' },
                {
                    xtype: 'radiogroup',
                    fieldLabel: 'SSH command mode',
                    columns: 1,
                    items: [
                        { boxLabel: 'Off', name: 'ssh_mode', inputValue: 'off' },
                        { boxLabel: 'Confirm each command', name: 'ssh_mode', inputValue: 'confirm' },
                        { boxLabel: 'Allowlist (read-only auto, rest confirm)', name: 'ssh_mode', inputValue: 'allowlist' },
                        { boxLabel: 'Full auto (denylist still requires approval)', name: 'ssh_mode', inputValue: 'auto' },
                    ],
                },
                { xtype: 'numberfield', name: 'max_commands_per_minute', fieldLabel: 'Max commands/min', minValue: 1, maxValue: 120 },
                { xtype: 'numberfield', name: 'session_max_minutes', fieldLabel: 'Session max (minutes)', minValue: 5, maxValue: 1440 },
            ],
        });

        me.categoryGrid = Ext.create('Ext.grid.Panel', {
            title: 'Permissions',
            store: Ext.create('Ext.data.Store', {
                fields: ['category', 'mode'],
                data: [],
            }),
            columns: [
                { text: 'Category', dataIndex: 'category', flex: 1 },
                {
                    text: 'Mode', dataIndex: 'mode', flex: 1,
                    editor: {
                        xtype: 'combo',
                        store: ['off', 'confirm', 'allow'],
                        forceSelection: true,
                    },
                },
            ],
            plugins: { ptype: 'cellediting', clicksToEdit: 1 },
        });

        me.providerGrid = Ext.create('Ext.grid.Panel', {
            title: 'Providers',
            store: Ext.create('Ext.data.Store', {
                fields: ['id', 'type', 'name', 'base_url', 'api_key', 'model', 'enabled'],
                data: [],
            }),
            columns: [
                { text: 'ID', dataIndex: 'id', width: 90 },
                { text: 'Type', dataIndex: 'type', width: 90 },
                { text: 'Name', dataIndex: 'name', flex: 1 },
                { text: 'Base URL', dataIndex: 'base_url', flex: 1 },
                { text: 'Model', dataIndex: 'model', flex: 1 },
                { text: 'Enabled', dataIndex: 'enabled', width: 70 },
            ],
            tbar: [
                { text: 'Add', handler: function() { me.editProvider(null); } },
                { text: 'Edit', handler: function() {
                    var sel = me.providerGrid.getSelection()[0];
                    if (sel) { me.editProvider(sel.data); }
                } },
                { text: 'Remove', handler: function() {
                    var sel = me.providerGrid.getSelection()[0];
                    if (sel) { me.providerGrid.getStore().remove(sel); }
                } },
                { text: 'Set Active', handler: function() {
                    var sel = me.providerGrid.getSelection()[0];
                    if (sel) { me.activeProvider = sel.get('id'); me.providerGrid.getView().refresh(); }
                } },
                { text: 'Test', handler: function() {
                    var sel = me.providerGrid.getSelection()[0];
                    if (sel) { me.testProvider(sel.data); }
                } },
            ],
        });

        me.auditGrid = Ext.create('Ext.grid.Panel', {
            title: 'Audit Log',
            store: Ext.create('Ext.data.Store', {
                fields: ['ts', 'event', 'actor', 'command', 'decision', 'status'],
                data: [],
            }),
            columns: [
                { text: 'Time', dataIndex: 'ts', width: 170 },
                { text: 'Event', dataIndex: 'event', width: 130 },
                { text: 'Actor', dataIndex: 'actor', width: 110 },
                { text: 'Command', dataIndex: 'command', flex: 1 },
                { text: 'Decision', dataIndex: 'decision', width: 90 },
                { text: 'Status', dataIndex: 'status', width: 80 },
            ],
            tbar: [{ text: 'Refresh', handler: function() { me.loadAudit(); } }],
        });

        me.items = [{
            xtype: 'tabpanel',
            items: [me.securityPanel, me.categoryGrid, me.providerGrid, me.auditGrid],
        }];

        me.buttons = [
            {
                text: 'KILL SWITCH: Disable AI',
                cls: 'x-btn-danger',
                handler: function() { me.toggleKillSwitch(); },
            },
            '->',
            { text: 'Save', handler: function() { me.save(); } },
            { text: 'Cancel', handler: function() { me.close(); } },
        ];

        me.callParent();
        me.loadConfig();
        me.loadAudit();
    },

    authHeaders: function() {
        return {
            'PVE-Auth-Cookie': Ext.util.Cookies.get('PVEAuthCookie') || '',
            'CSRFPreventionToken': (typeof Proxmox !== 'undefined' && Proxmox.CSRFPreventionToken) || '',
            'Content-Type': 'application/json',
        };
    },

    loadConfig: function() {
        var me = this;
        fetch(me.backendUrl + '/config', { headers: me.authHeaders() })
            .then(function(r) { return r.json(); })
            .then(function(cfg) {
                me.cfg = cfg;
                me.activeProvider = cfg.active_provider;
                me.securityPanel.getForm().setValues(cfg.security);
                me.categoryGrid.getStore().loadData(
                    Object.keys(cfg.security.categories).map(function(c) {
                        return { category: c, mode: cfg.security.categories[c] };
                    })
                );
                me.providerGrid.getStore().loadData(cfg.providers || []);
            })
            .catch(function(e) { Ext.Msg.alert('Error', 'Failed to load config: ' + e); });
    },

    loadAudit: function() {
        var me = this;
        fetch(me.backendUrl + '/audit?limit=200', { headers: me.authHeaders() })
            .then(function(r) { return r.json(); })
            .then(function(data) { me.auditGrid.getStore().loadData(data.entries.reverse()); });
    },

    editProvider: function(existing) {
        var me = this;
        var win = Ext.create('Ext.window.Window', {
            title: existing ? 'Edit Provider' : 'Add Provider',
            modal: true,
            width: 420,
            layout: 'fit',
            items: [{
                xtype: 'form',
                bodyPadding: 10,
                defaults: { anchor: '100%' },
                items: [
                    { xtype: 'textfield', name: 'id', fieldLabel: 'ID', value: existing && existing.id },
                    {
                        xtype: 'combo', name: 'type', fieldLabel: 'Type',
                        store: ['vllm', 'ollama', 'llamacpp', 'openai', 'anthropic', 'openrouter', 'compatible'],
                        forceSelection: true, value: existing && existing.type,
                    },
                    { xtype: 'textfield', name: 'name', fieldLabel: 'Name', value: existing && existing.name },
                    { xtype: 'textfield', name: 'base_url', fieldLabel: 'Base URL', value: existing && existing.base_url },
                    { xtype: 'textfield', name: 'api_key', fieldLabel: 'API Key', inputType: 'password', value: existing && existing.api_key },
                    { xtype: 'textfield', name: 'model', fieldLabel: 'Model', value: existing && existing.model },
                ],
            }],
            buttons: [{
                text: 'OK',
                handler: function() {
                    var vals = win.down('form').getValues();
                    vals.enabled = true;
                    if (existing) {
                        var rec = me.providerGrid.getStore().findRecord('id', existing.id);
                        if (rec) { rec.set(vals); rec.commit(); }
                    } else {
                        me.providerGrid.getStore().add(vals);
                    }
                    win.close();
                },
            }],
        });
        win.show();
    },

    testProvider: function(p) {
        fetch(this.backendUrl + '/providers/test', {
            method: 'POST',
            headers: this.authHeaders(),
            body: JSON.stringify({ type: p.type, base_url: p.base_url, api_key: p.api_key, model: p.model }),
        }).then(function(r) { return r.json(); })
          .then(function(res) {
              Ext.Msg.alert('Provider Test', res.ok ? 'OK: ' + res.message : 'FAILED: ' + (res.message || res.detail));
          });
    },

    save: function() {
        var me = this;
        var sec = me.securityPanel.getForm().getValues();
        var categories = {};
        me.categoryGrid.getStore().each(function(rec) {
            categories[rec.get('category')] = rec.get('mode');
        });
        var cfg = me.cfg;
        cfg.security = {
            server_access: !!sec.server_access,
            internet_access: !!sec.internet_access,
            online_providers: !!sec.online_providers,
            guest_exec: !!sec.guest_exec,
            ssh_mode: sec.ssh_mode || 'off',
            max_commands_per_minute: sec.max_commands_per_minute || 10,
            session_max_minutes: sec.session_max_minutes || 120,
            categories: categories,
        };
        cfg.active_provider = me.activeProvider || cfg.active_provider;
        cfg.providers = [];
        me.providerGrid.getStore().each(function(rec) { cfg.providers.push(rec.data); });

        fetch(me.backendUrl + '/config', {
            method: 'PUT',
            headers: me.authHeaders(),
            body: JSON.stringify(cfg),
        }).then(function(r) {
            if (r.ok) { me.close(); }
            else { r.json().then(function(e) { Ext.Msg.alert('Save failed', e.detail || r.status); }); }
        });
    },

    toggleKillSwitch: function() {
        Ext.Msg.confirm('Kill Switch', 'Immediately disable ALL AI chat and execution?', function(btn) {
            if (btn !== 'yes') { return; }
            // kill switch is a server-side file; ask backend to note intent via audit
            Ext.Msg.alert('Kill Switch',
                'To activate: touch /etc/proxmox-ai/DISABLED on the backend CT. ' +
                'Remove the file to re-enable. (UI-driven toggle lands in v1.1.)');
        });
    },
});
