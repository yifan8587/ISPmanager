/**
 * service_manager/order_form.js
 * 服务工单表单 — 根据业务类型动态显示/隐藏对应字段区块
 */
(function () {
  'use strict';

  /* ── 各业务类型对应需要显示的 fieldset class ── */
  var SECTION_MAP = {
    vps:        ['vps-section'],
    edge_gre:   ['edge-common-section', 'edge-gre-section'],
    edge_wg:    ['edge-common-section', 'edge-wg-section'],
    edge_socks5:['edge-common-section', 'edge-socks5-section'],
    ip:         ['ip-section'],
    bandwidth:  ['bandwidth-section'],
  };

  /* 所有可能出现的专属 section class */
  var ALL_SECTIONS = [
    'vps-section',
    'edge-common-section',
    'edge-gre-section',
    'edge-wg-section',
    'edge-socks5-section',
    'ip-section',
    'bandwidth-section',
  ];

  function getEl(id) {
    return document.getElementById(id);
  }

  function toggleSections(businessType) {
    var show = SECTION_MAP[businessType] || [];

    ALL_SECTIONS.forEach(function (cls) {
      var els = document.querySelectorAll('fieldset.' + cls);
      els.forEach(function (el) {
        el.style.display = show.indexOf(cls) !== -1 ? '' : 'none';
      });
    });

    /* 同步更新列表筛选提示标签颜色 */
    var label = getEl('business-type-indicator');
    if (label) {
      var COLORS = {
        vps: '#007bff', edge_gre: '#6f42c1', edge_wg: '#6f42c1',
        edge_socks5: '#fd7e14', ip: '#17a2b8', bandwidth: '#28a745',
      };
      label.style.background = COLORS[businessType] || '#6c757d';
    }
  }

  function init() {
    var select = getEl('id_business_type');
    if (!select) return;

    /* 初始渲染 */
    toggleSections(select.value);

    /* 监听变化 */
    select.addEventListener('change', function () {
      toggleSections(this.value);
    });
  }

  /* DOMContentLoaded 或 Django Admin 的 ready 事件 */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
