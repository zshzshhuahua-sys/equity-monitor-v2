/**
 * 股权激励监控面板 - 前端主逻辑
 */

// 全局状态
let stocksData = [];
let isMonitoring = false;
let autoRefreshInterval = null;
let isEditMode = false;
let selectedStocks = new Set();

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    initDashboard();
    
    // 直接绑定添加按钮事件，确保一定生效
    const addBtn = document.getElementById('add-stock-btn');
    if (addBtn) {
        addBtn.onclick = function() {
            openAddStockModal();
        };
    }
});

async function initDashboard() {
    await loadPrices();
    await checkMonitorStatus();
    setupEventListeners();
    // 图表功能已禁用
    // initCharts();
    
    // 自动刷新（每30秒）
    autoRefreshInterval = setInterval(loadPrices, 30000);
}

// 设置事件监听
function setupEventListeners() {
    // 刷新按钮
    // 刷新按钮直接调用
function doRefresh() {
    const btn = document.getElementById('refresh-btn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '⏳ 加载中...';
    btn.disabled = true;
    
    loadPrices().finally(() => {
        btn.innerHTML = originalText;
        btn.disabled = false;
    });
}
    
    // 监控开关
    document.getElementById('monitor-toggle-btn').addEventListener('click', toggleMonitor);
    
    // 文件导入
    document.getElementById('import-file').addEventListener('change', handleFileImport);
    
    // 手工添加按钮 - 使用id选择器绑定
    const addBtn = document.getElementById('add-stock-btn');
    if (addBtn) {
        addBtn.addEventListener('click', openAddStockModal);
    }
    
    // 预警弹窗
    document.getElementById('close-alert-btn').addEventListener('click', closeAlertModal);
    document.getElementById('ack-all-btn').addEventListener('click', acknowledgeAllAlerts);
    
    // 编辑弹窗
    document.getElementById('cancel-edit-btn').addEventListener('click', closeEditModal);
    document.getElementById('save-edit-btn').addEventListener('click', saveEdit);
    
    // 手工添加股票
    document.getElementById('cancel-add-btn').addEventListener('click', closeAddStockModal);
    document.getElementById('save-add-btn').addEventListener('click', saveAddStock);
}

// 切换编辑模式
function toggleEditMode() {
    isEditMode = !isEditMode;
    const editBtn = document.getElementById('edit-mode-btn');
    const selectAllCheckbox = document.getElementById('select-all');
    const batchDeleteBtn = document.getElementById('batch-delete-btn');
    
    if (isEditMode) {
        editBtn.textContent = '✓ 完成';
        editBtn.classList.remove('bg-blue-100', 'text-blue-700');
        editBtn.classList.add('bg-green-100', 'text-green-700');
        selectAllCheckbox.style.display = 'inline';
        batchDeleteBtn.style.display = 'inline';
    } else {
        editBtn.textContent = '✏️ 编辑';
        editBtn.classList.remove('bg-green-100', 'text-green-700');
        editBtn.classList.add('bg-blue-100', 'text-blue-700');
        selectAllCheckbox.style.display = 'none';
        batchDeleteBtn.style.display = 'none';
        selectAllCheckbox.checked = false;
        selectedStocks.clear();
        updateTableSelection();
    }
    
    // 重新渲染表格以显示/隐藏复选框
    updateTable(stocksData);
}

// 切换全选
function toggleSelectAll() {
    const selectAllCheckbox = document.getElementById('select-all');
    if (selectAllCheckbox.checked) {
        // 全选
        stocksData.forEach(stock => selectedStocks.add(stock.full_code));
    } else {
        // 取消全选
        selectedStocks.clear();
    }
    updateTableSelection();
}

// 更新表格选择状态
function updateTableSelection() {
    const checkboxes = document.querySelectorAll('.stock-checkbox');
    checkboxes.forEach(checkbox => {
        checkbox.checked = selectedStocks.has(checkbox.dataset.fullCode);
    });
    
    // 更新删除按钮状态
    const batchDeleteBtn = document.getElementById('batch-delete-btn');
    if (selectedStocks.size > 0) {
        batchDeleteBtn.textContent = `🗑️ 删除 (${selectedStocks.size})`;
        batchDeleteBtn.classList.remove('bg-red-100', 'text-red-700');
        batchDeleteBtn.classList.add('bg-red-600', 'text-white');
    } else {
        batchDeleteBtn.textContent = '🗑️ 删除';
        batchDeleteBtn.classList.remove('bg-red-600', 'text-white');
        batchDeleteBtn.classList.add('bg-red-100', 'text-red-700');
    }
}

// 切换单个选择
function toggleStockSelection(fullCode) {
    if (selectedStocks.has(fullCode)) {
        selectedStocks.delete(fullCode);
    } else {
        selectedStocks.add(fullCode);
    }
    updateTableSelection();
    
    // 更新全选框状态
    const selectAllCheckbox = document.getElementById('select-all');
    if (selectedStocks.size === stocksData.length) {
        selectAllCheckbox.checked = true;
    } else if (selectedStocks.size === 0) {
        selectAllCheckbox.checked = false;
    }
}

// 批量删除
async function batchDelete() {
    if (selectedStocks.size === 0) {
        showNotification('提示', '请先选择要删除的股票', 'warning');
        return;
    }
    
    if (!confirm(`确定要删除选中的 ${selectedStocks.size} 只股票吗？`)) {
        return;
    }
    
    try {
        const response = await fetch('/api/stocks/batch-delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ full_codes: Array.from(selectedStocks) })
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '删除失败');
        }
        
        const result = await response.json();
        showNotification('删除成功', `已删除 ${result.deleted} 只股票`, 'success');
        
        // 清空选择并刷新
        selectedStocks.clear();
        document.getElementById('select-all').checked = false;
        updateTableSelection();
        loadPrices();
        
    } catch (error) {
        console.error('删除失败:', error);
        showNotification('删除失败', error.message, 'error');
    }
}

// 打开编辑弹窗
function openEditModal(fullCode) {
    const stock = stocksData.find(s => s.full_code === fullCode);
    if (!stock) return;
    
    document.getElementById('edit-symbol').value = stock.full_code;
    document.getElementById('edit-name').value = stock.name || '';
    document.getElementById('edit-strike-price').value = stock.strike_price;
    // 如果有quantity字段则填充
    const qtyEl = document.getElementById('edit-quantity');
    if (qtyEl) {
        qtyEl.value = stock.quantity || '';
    }
    document.getElementById('edit-threshold').value = stock.custom_threshold ? (stock.custom_threshold * 100).toFixed(2) : '';
    document.getElementById('edit-notes').value = stock.notes || '';
    
    document.getElementById('edit-modal').classList.remove('hidden');
    document.getElementById('edit-modal').classList.add('flex');
}

// 关闭编辑弹窗
function closeEditModal() {
    document.getElementById('edit-modal').classList.add('hidden');
    document.getElementById('edit-modal').classList.remove('flex');
}

// 保存编辑
async function saveEdit() {
    const fullCode = document.getElementById('edit-symbol').value;
    const name = document.getElementById('edit-name').value;
    const strikePrice = parseFloat(document.getElementById('edit-strike-price').value);
    const quantity = document.getElementById('edit-quantity') ? document.getElementById('edit-quantity').value : null;
    const thresholdPercent = document.getElementById('edit-threshold').value;
    const notes = document.getElementById('edit-notes').value;
    
    // 验证：行权价格不能为0
    if (!strikePrice || strikePrice <= 0) {
        showNotification('错误', '行权价格必须大于0', 'error');
        return;
    }
    
    // 验证：数量不能为0（如果有填写的话）
    if (quantity !== null && quantity !== '' && parseInt(quantity) <= 0) {
        showNotification('错误', '持有数量必须大于0', 'error');
        return;
    }
    
    // 验证：预警阈值不能为0（如果有填写的话）
    if (thresholdPercent !== '' && parseFloat(thresholdPercent) <= 0) {
        showNotification('错误', '预警阈值必须大于0', 'error');
        return;
    }
    
    const data = {
        full_code: fullCode,
        name: name || null,
        strike_price: strikePrice,
        quantity: (quantity === '' || quantity === null) ? null : parseInt(quantity),
        custom_threshold: thresholdPercent ? parseFloat(thresholdPercent) / 100 : null,
        notes: notes || null
    };
    
    try {
        const response = await fetch('/api/stocks/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '保存失败');
        }
        
        showNotification('保存成功', '股票信息已更新', 'success');
        closeEditModal();
        loadPrices();
        
    } catch (error) {
        console.error('保存失败:', error);
        showNotification('保存失败', error.message, 'error');
    }
}

// 加载价格数据
async function loadPrices() {
    try {
        const response = await fetch('/api/monitor/prices');
        if (!response.ok) throw new Error('获取价格失败');
        
        stocksData = await response.json();
        updateTable(stocksData);
        updateStats(stocksData);
        // 图表功能已禁用
        // updateCharts(stocksData);
        
    } catch (error) {
        console.error('加载价格失败:', error);
        showNotification('加载失败', '无法获取价格数据', 'error');
    }
}

// 检查监控状态
async function checkMonitorStatus() {
    try {
        const response = await fetch('/api/monitor/status');
        if (!response.ok) return;
        
        const status = await response.json();
        isMonitoring = status.is_running;
        updateMonitorButton();
        updateMarketStatus(status.is_trading_time);
        
    } catch (error) {
        console.error('检查监控状态失败:', error);
    }
}

// 更新监控按钮状态
function updateMonitorButton() {
    const btn = document.getElementById('monitor-toggle-btn');
    if (isMonitoring) {
        btn.textContent = '⏹️ 停止监控';
        btn.classList.remove('bg-green-600', 'hover:bg-green-700');
        btn.classList.add('bg-red-600', 'hover:bg-red-700');
    } else {
        btn.textContent = '▶️ 启动监控';
        btn.classList.remove('bg-red-600', 'hover:bg-red-700');
        btn.classList.add('bg-green-600', 'hover:bg-green-700');
    }
}

// 更新市场状态
function updateMarketStatus(isTrading) {
    const statusEl = document.getElementById('market-status');
    if (isTrading) {
        statusEl.textContent = '🟢 交易中';
        statusEl.className = 'px-3 py-1 rounded-full text-sm font-medium bg-green-100 text-green-800';
    } else {
        statusEl.textContent = '⚪ 休市中';
        statusEl.className = 'px-3 py-1 rounded-full text-sm font-medium bg-gray-100 text-gray-600';
    }
}

// 切换监控状态
async function toggleMonitor() {
    try {
        const url = isMonitoring ? '/api/monitor/stop' : '/api/monitor/start';
        const response = await fetch(url, { method: 'POST' });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '操作失败');
        }
        
        isMonitoring = !isMonitoring;
        updateMonitorButton();
        showNotification(
            isMonitoring ? '监控已启动' : '监控已停止',
            isMonitoring ? '系统将自动监控价格变化' : '监控服务已停止',
            'success'
        );
        
    } catch (error) {
        console.error('切换监控状态失败:', error);
        showNotification('操作失败', error.message, 'error');
    }
}

// 更新表格
function updateTable(data) {
    const tbody = document.getElementById('stocks-tbody');
    tbody.innerHTML = '';
    
    data.forEach(stock => {
        const row = document.createElement('tr');
        row.className = 'hover:bg-gray-50';
        

        
        // 复选框列（编辑模式下显示）
        const checkboxCell = isEditMode ? `
            <td class="px-3 py-4 whitespace-nowrap">
                <input type="checkbox" class="stock-checkbox" 
                       data-full-code="${stock.full_code}" data-id="${stock.id}"
                       ${selectedStocks.has(stock.full_code) ? 'checked' : ''}
                       onchange="toggleStockSelection('${stock.full_code}')">
            </td>
        ` : '<td class="px-3 py-4 whitespace-nowrap"></td>';
        
        // 格式化添加日期
        const createdDate = stock.created_at ? new Date(stock.created_at).toLocaleDateString() : '-';
        
        // 股票代码列（双击删除）
        const codeCell = isEditMode ? `
            <td class="px-3 py-4 whitespace-nowrap text-sm cursor-pointer hover:text-red-600"
                ondblclick="deleteStock('${stock.full_code}')" title="双击删除">
                ${stock.symbol}
            </td>
        ` : `
            <td class="px-3 py-4 whitespace-nowrap text-sm text-gray-500">
                ${stock.symbol}
            </td>
        `;
        
        // 股票名称列（编辑模式下可点击）
        const nameCell = isEditMode ? `
            <td class="px-3 py-4 whitespace-nowrap cursor-pointer hover:text-blue-600"
                onclick="openEditModal('${stock.full_code}')">
                <div class="text-sm font-medium text-gray-900">${stock.name || '-'}</div>
                <div class="text-xs text-blue-500">✏️</div>
            </td>
        ` : `
            <td class="px-3 py-4 whitespace-nowrap">
                <div class="text-sm font-medium text-gray-900">${stock.name || '-'}</div>
            </td>
        `;
        
        // 计算溢价率 = 最新价/行权价
        const premiumRate = stock.strike_price > 0 ? (stock.current_price / stock.strike_price * 100) : 0;
        
        // 根据溢价率设置颜色
        let premiumColorClass = 'text-gray-500';
        if (premiumRate >= 150) {
            premiumColorClass = 'text-red-600';  // 红色 >=150%
        } else if (premiumRate >= 80) {
            premiumColorClass = 'text-yellow-600';  // 黄色 80%-150%
        } else {
            premiumColorClass = 'text-green-600';  // 绿色 <80%
        }
        
        // 设置预警状态图标
        let alertEmoji = '🟢';
        let alertClass = 'text-green-600';
        if (premiumRate >= 150) {
            alertEmoji = '🔴';
            alertClass = 'text-red-600';
        } else if (premiumRate >= 80) {
            alertEmoji = '🟡';
            alertClass = 'text-yellow-600';
        }
        
        row.innerHTML = `
            ${checkboxCell}
            <td class="px-3 py-4 whitespace-nowrap text-sm text-gray-500">${stock.id}</td>
            <td class="px-3 py-4 whitespace-nowrap text-sm text-gray-500">${createdDate}</td>
            ${codeCell}
            ${nameCell}
            <td class="px-3 py-4 whitespace-nowrap text-sm text-gray-900">
                ¥${stock.current_price.toFixed(2)}
            </td>
            <td class="px-3 py-4 whitespace-nowrap text-sm text-gray-500">
                ¥${stock.strike_price.toFixed(2)}
            </td>
            <td class="px-3 py-4 whitespace-nowrap text-sm font-medium ${premiumColorClass}">
                ${premiumRate.toFixed(2)}%
            </td>
            <td class="px-3 py-4 whitespace-nowrap">
                <span class="${alertClass} text-lg">${alertEmoji}</span>
            </td>
            <td class="px-3 py-4 whitespace-nowrap text-sm text-gray-500">
                ${stock.notes || '-'}
            </td>
        `;
        
        tbody.appendChild(row);
    });
    
    // 初始化DataTables（如果尚未初始化）
    if (!$.fn.DataTable.isDataTable('#stocks-table')) {
        $('#stocks-table').DataTable({
            pageLength: 25,
            language: {
                search: '搜索:',
                lengthMenu: '显示 _MENU_ 条',
                info: '显示 _START_ 到 _END_ 条，共 _TOTAL_ 条',
                paginate: {
                    first: '首页',
                    last: '末页',
                    next: '下页',
                    previous: '上页'
                }
            }
        });
    }
}

// 更新统计
function updateStats(data) {
    document.getElementById('total-stocks').textContent = data.length;
    
    let green = 0;  // <80%
    let yellow = 0;  // 80%-150%
    let red = 0;     // >=150%
    
    data.forEach(s => {
        const premiumRate = s.strike_price > 0 ? (s.current_price / s.strike_price * 100) : 0;
        if (premiumRate >= 150) {
            red++;
        } else if (premiumRate >= 80) {
            yellow++;
        } else {
            green++;
        }
    });
    
    document.getElementById('normal-count').textContent = green;
    document.getElementById('watch-count').textContent = yellow;
    document.getElementById('critical-count').textContent = red;
}

// 获取预警级别样式
function getAlertClass(level) {
    const classes = {
        'normal': 'alert-normal',
        'watch': 'alert-watch',
        'warning': 'alert-warning',
        'critical': 'alert-critical'
    };
    return classes[level] || 'text-gray-400';
}

// 获取预警级别表情
function getAlertEmoji(level) {
    const emojis = {
        'normal': '🟢',
        'watch': '🟡',
        'warning': '🟠',
        'critical': '🔴'
    };
    return emojis[level] || '⚪';
}

// 文件导入处理
async function handleFileImport(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    const formData = new FormData();
    const endpoint = file.name.endsWith('.csv') ? '/api/import/csv' : '/api/import/excel';
    formData.append('file', file);
    
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '导入失败');
        }
        
        const result = await response.json();
        showNotification(
            '导入成功',
            `成功导入 ${result.imported} 条，更新 ${result.updated} 条${result.failed > 0 ? '，失败 ' + result.failed + ' 条' : ''}`,
            'success'
        );
        
        // 刷新数据
        loadPrices();
        
    } catch (error) {
        console.error('导入失败:', error);
        showNotification('导入失败', error.message, 'error');
    }
    
    // 清空文件输入
    event.target.value = '';
}

// 显示通知
function showNotification(title, message, type = 'info') {
    // 创建提示元素
    const notification = document.createElement('div');
    notification.className = `fixed top-4 right-4 px-6 py-4 rounded-lg shadow-lg z-50 transform transition-all duration-300`;
    
    // 根据类型设置颜色
    const colors = {
        success: 'bg-green-500 text-white',
        error: 'bg-red-500 text-white',
        warning: 'bg-yellow-500 text-white',
        info: 'bg-blue-500 text-white'
    };
    notification.className += ` ${colors[type] || colors.info}`;
    
    notification.innerHTML = `
        <div class="flex items-center">
            <span class="font-semibold">${title}</span>
            <span class="ml-2">${message}</span>
        </div>
    `;
    
    document.body.appendChild(notification);
    
    // 3秒后自动消失
    setTimeout(() => {
        notification.classList.add('opacity-0', 'transform', 'translate-y-2');
        setTimeout(() => notification.remove(), 300);
    }, 3000);
    
    // 同时尝试浏览器通知
    if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(title, { body: message });
    }
}

// 关闭预警弹窗
function closeAlertModal() {
    document.getElementById('alert-modal').classList.add('hidden');
    document.getElementById('alert-modal').classList.remove('flex');
}

// 确认所有预警
async function acknowledgeAllAlerts() {
    try {
        const response = await fetch('/api/monitor/alerts/acknowledge-all', {
            method: 'POST'
        });
        
        if (!response.ok) throw new Error('操作失败');
        
        const result = await response.json();
        showNotification('操作成功', `已确认 ${result.count} 条预警`, 'success');
        closeAlertModal();
        
    } catch (error) {
        console.error('确认预警失败:', error);
        showNotification('操作失败', error.message, 'error');
    }
}

// 请求浏览器通知权限
if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
}

// 打开手工添加弹窗
function openAddStockModal() {
    document.getElementById('add-symbol').value = '';
    document.getElementById('add-strike-price').value = '';
    document.getElementById('add-quantity').value = '';
    document.getElementById('add-threshold').value = '';
    document.getElementById('add-notes').value = '';
    
    document.getElementById('add-stock-modal').classList.remove('hidden');
    document.getElementById('add-stock-modal').classList.add('flex');
}

// 关闭手工添加弹窗
function closeAddStockModal() {
    document.getElementById('add-stock-modal').classList.add('hidden');
    document.getElementById('add-stock-modal').classList.remove('flex');
}

// 保存手工添加
async function saveAddStock() {
    const symbol = document.getElementById('add-symbol').value.trim();
    const strikePrice = document.getElementById('add-strike-price').value;
    
    // 验证
    if (!symbol) {
        showNotification('错误', '请输入股票代码', 'error');
        return;
    }
    if (!strikePrice || parseFloat(strikePrice) <= 0) {
        showNotification('错误', '行权价格必须大于0', 'error');
        return;
    }
    
    const quantity = document.getElementById('add-quantity').value;
    const thresholdPercent = document.getElementById('add-threshold').value;
    const notes = document.getElementById('add-notes').value;
    
    const data = {
        symbol: symbol,
        strike_price: parseFloat(strikePrice),
        quantity: quantity ? parseInt(quantity) : null,
        custom_threshold: thresholdPercent ? parseFloat(thresholdPercent) / 100 : null,
        notes: notes || null
    };
    
    showNotification('提示', '正在添加...', 'info');
    
    try {
        const response = await fetch('/api/stocks/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        
        const result = await response.json();
        
        if (!response.ok) {
            throw new Error(result.detail || '添加失败');
        }
        
        showNotification('成功', result.message, 'success');
        closeAddStockModal();
        loadPrices();
        
    } catch (error) {
        showNotification('失败', error.message, 'error');
    }
}

// 删除股票（双击触发）
async function deleteStock(fullCode) {
    if (!confirm(`确定要删除 ${fullCode} 吗？`)) {
        return;
    }
    
    try {
        const response = await fetch('/api/stocks/batch-delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ full_codes: [fullCode] })
        });
        
        const result = await response.json();
        
        if (!response.ok) {
            throw new Error(result.detail || '删除失败');
        }
        
        showNotification('成功', `已删除 ${fullCode}`, 'success');
        loadPrices();
        
    } catch (error) {
        showNotification('失败', error.message, 'error');
    }
}
