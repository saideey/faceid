// API Client for Davomat Tizimi
// Barcha backend endpoint'lar uchun JavaScript client

const API_BASE_URL = window.location.origin;

// Get token from localStorage
function getToken() {
    return localStorage.getItem('token');
}

// Set token to localStorage
function setToken(token) {
    localStorage.setItem('token', token);
}

// Remove token
function removeToken() {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
}

// Get current user
function getCurrentUser() {
    const userStr = localStorage.getItem('user');
    return userStr ? JSON.parse(userStr) : null;
}

// Set current user
function setCurrentUser(user) {
    localStorage.setItem('user', JSON.stringify(user));
}

// Make API request
async function apiRequest(endpoint, options = {}) {
    const url = `${API_BASE_URL}${endpoint}`;

    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    const token = getToken();
    console.log('🔑 Token from localStorage:', token ? token.substring(0, 30) + '...' : 'NO TOKEN');

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
        console.log('📤 Sending Authorization header:', `Bearer ${token.substring(0, 30)}...`);
    } else {
        console.warn('⚠️ No token available for request');
    }

    console.log('🌐 API Request:', {
        url,
        method: options.method || 'GET',
        hasToken: !!token
    });

    const config = {
        ...options,
        headers
    };

    try {
        const response = await fetch(url, config);

        console.log('📥 Response:', {
            status: response.status,
            statusText: response.statusText,
            contentType: response.headers.get('content-type')
        });

        // Handle 401 Unauthorized
        if (response.status === 401) {
            console.error('Unauthorized - redirecting to login');
            removeToken();
            if (window.location.pathname !== '/login') {
                window.location.href = '/login';
            }
            throw new Error('Unauthorized - Please login');
        }

        // Handle 403 Forbidden
        if (response.status === 403) {
            throw new Error('Access denied - Insufficient permissions');
        }

        // Handle 404 Not Found
        if (response.status === 404) {
            throw new Error('Resource not found');
        }

        // Try to parse JSON
        const contentType = response.headers.get('content-type');
        if (!contentType || !contentType.includes('application/json')) {
            // Not JSON response - probably HTML error page
            const text = await response.text();
            console.error('Non-JSON response:', text.substring(0, 200));
            throw new Error('Server returned non-JSON response. Check if backend is running correctly.');
        }

        const data = await response.json();

        if (!response.ok) {
            throw {
                status: response.status,
                message: data.message || data.error || 'An error occurred',
                response: { data }
            };
        }

        // Handle different response formats
        // Backend returns: {success: true, data: {...}, message: "..."}
        // We need to return just the data part for most endpoints
        if (data.success && data.data !== undefined) {
            return data.data;
        }

        // Some endpoints might return data directly
        if (data.data !== undefined) {
            return data.data;
        }

        // Fallback: return entire response
        return data;

    } catch (error) {
        console.error('API Request Error:', error);
        throw error;
    }
}

// API Object
const API = {
    // Authentication
    auth: {
        login: (credentials) => apiRequest('/api/auth/login', {
            method: 'POST',
            body: JSON.stringify(credentials)
        }),

        superAdminLogin: (credentials) => apiRequest('/api/superadmin/login', {
            method: 'POST',
            body: JSON.stringify(credentials)
        }),

        logout: () => {
            removeToken();
            window.location.href = '/login';
        }
    },

    // Companies (Super Admin)
    companies: {
        list: (params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/superadmin/companies?${queryString}`);
        },

        get: (id) => apiRequest(`/api/superadmin/companies/${id}`),

        create: (data) => apiRequest('/api/superadmin/companies', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        update: (id, data) => apiRequest(`/api/superadmin/companies/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        }),

        delete: (id) => apiRequest(`/api/superadmin/companies/${id}`, {
            method: 'DELETE'
        })
    },

    // Branches
    branches: {
        list: (params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/branches?${queryString}`);
        },

        get: (id) => apiRequest(`/api/branches/${id}`),

        create: (data) => apiRequest('/api/branches', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        update: (id, data) => apiRequest(`/api/branches/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        }),

        delete: (id) => apiRequest(`/api/branches/${id}`, {
            method: 'DELETE'
        }),

        getEmployees: (id) => apiRequest(`/api/branches/${id}/employees`)
    },

    // Departments
    departments: {
        list: (params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/departments?${queryString}`);
        },

        get: (id) => apiRequest(`/api/departments/${id}`),

        create: (data) => apiRequest('/api/departments', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        update: (id, data) => apiRequest(`/api/departments/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        }),

        delete: (id) => apiRequest(`/api/departments/${id}`, {
            method: 'DELETE'
        })
    },

    // Employees
    employees: {
        list: (params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/employees?${queryString}`);
        },

        get: (id) => apiRequest(`/api/employees/${id}`),

        create: (data) => apiRequest('/api/employees', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        update: (id, data) => apiRequest(`/api/employees/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        }),

        delete: (id) => apiRequest(`/api/employees/${id}`, {
            method: 'DELETE'
        }),

        // Schedule
        getSchedule: (employeeId) => apiRequest(`/api/employees/${employeeId}/schedule`),

        setSchedule: (employeeId, data) => apiRequest(`/api/employees/${employeeId}/schedule`, {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        updateDaySchedule: (employeeId, dayOfWeek, data) => apiRequest(`/api/employees/${employeeId}/schedule/${dayOfWeek}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        }),

        deleteDaySchedule: (employeeId, dayOfWeek) => apiRequest(`/api/employees/${employeeId}/schedule/${dayOfWeek}`, {
            method: 'DELETE'
        }),

        setBulkSchedule: (employeeId, data) => apiRequest(`/api/employees/${employeeId}/schedule/bulk`, {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        copySchedule: (employeeId, sourceEmployeeId) => apiRequest(`/api/employees/${employeeId}/schedule/copy-from/${sourceEmployeeId}`, {
            method: 'POST'
        })
    },

    // Attendance
    attendance: {
        list: (params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/attendance/date-range?${queryString}`);
        },

        checkIn: (companyId, branchId, data) => apiRequest(`/api/terminal/${companyId}/${branchId}/checkin`, {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        checkOut: (companyId, branchId, data) => apiRequest(`/api/terminal/${companyId}/${branchId}/checkout`, {
            method: 'POST',
            body: JSON.stringify(data)
        })
    },

    // Penalties
    penalties: {
        list: (params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/penalties?${queryString}`);
        },

        get: (id) => apiRequest(`/api/penalties/${id}`),

        create: (data) => apiRequest('/api/penalties/create', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        delete: (id) => apiRequest(`/api/penalties/${id}`, {
            method: 'DELETE'
        }),

        waive: (id, data) => apiRequest(`/api/penalties/${id}/waive`, {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        restore: (id) => apiRequest(`/api/penalties/${id}/restore`, {
            method: 'POST'
        }),

        excuse: (id, data) => apiRequest(`/api/penalties/${id}/excuse`, {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        unexcuse: (id) => apiRequest(`/api/penalties/${id}/unexcuse`, {
            method: 'POST'
        }),

        bulkExcuse: (data) => apiRequest('/api/penalties/bulk-excuse', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        bulkWaive: (data) => apiRequest('/api/penalties/bulk-waive', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        getSummary: (employeeId, params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/penalties/employee/${employeeId}/summary?${queryString}`);
        }
    },

    // Bonuses
    bonuses: {
        list: (params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/bonuses?${queryString}`);
        },

        get: (id) => apiRequest(`/api/bonuses/${id}`),

        create: (data) => apiRequest('/api/bonuses/create', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        bulkCreate: (data) => apiRequest('/api/bonuses/bulk-create', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        delete: (id) => apiRequest(`/api/bonuses/${id}`, {
            method: 'DELETE'
        }),

        getSummary: (employeeId, params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/bonuses/employee/${employeeId}/summary?${queryString}`);
        },

        autoCalculatePerfectAttendance: (data) => apiRequest('/api/bonuses/auto-calculate/perfect-attendance', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        autoCalculateEarlyArrival: (data) => apiRequest('/api/bonuses/auto-calculate/early-arrival', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        getLeaderboard: (params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/bonuses/leaderboard?${queryString}`);
        }
    },

    // Salary
    salary: {
        calculate: (data) => apiRequest('/api/salary/calculate', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        bulkCalculate: (data) => apiRequest('/api/salary/bulk-calculate', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        monthlyReport: (data) => apiRequest('/api/salary/monthly-report', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        customReport: (data) => apiRequest('/api/salary/custom-report', {
            method: 'POST',
            body: JSON.stringify(data)
        }),

        getLateRanking: (params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/salary/late-ranking?${queryString}`);
        },

        getAttendanceRanking: (params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/salary/attendance-ranking?${queryString}`);
        },

        getPayrollSummary: (params = {}) => {
            const queryString = new URLSearchParams(params).toString();
            return apiRequest(`/api/salary/payroll-summary?${queryString}`);
        }
    },

    // Settings
    settings: {
        get: () => apiRequest('/api/settings'),

        update: (data) => apiRequest('/api/settings', {
            method: 'PUT',
            body: JSON.stringify(data)
        }),

        uploadLogo: async (formData) => {
            const token = getToken();
            const response = await fetch('/api/settings/logo', {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`
                },
                body: formData  // Don't set Content-Type for FormData
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.message || 'Upload failed');
            }

            return response.json();
        },

        deleteLogo: () => apiRequest('/api/settings/logo', {
            method: 'DELETE'
        })
    },

    // Export
    export: {
        employees: async (params = {}) => {
            try {
                const queryString = new URLSearchParams(params).toString();
                const url = `/api/export/employees?${queryString}`;

                const token = getToken();

                // Fetch with auth header
                const response = await fetch(url, {
                    method: 'GET',
                    headers: {
                        'Authorization': `Bearer ${token}`
                    }
                });

                if (!response.ok) {
                    throw new Error('Export failed');
                }

                // Get filename from header
                const contentDisposition = response.headers.get('Content-Disposition');
                let filename = 'Xodimlar.xlsx';
                if (contentDisposition) {
                    const filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
                    if (filenameMatch && filenameMatch[1]) {
                        filename = filenameMatch[1].replace(/['"]/g, '');
                    }
                }

                // Download blob
                const blob = await response.blob();
                const downloadUrl = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = downloadUrl;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(downloadUrl);
                document.body.removeChild(a);

                return true;
            } catch (error) {
                console.error('Export error:', error);
                throw error;
            }
        }
    },

    // Reports
    reports: {
        attendanceData: (params) => apiRequest(`/api/reports/attendance?${new URLSearchParams(params)}`),
        salaryData: (params) => apiRequest(`/api/reports/salary?${new URLSearchParams(params)}`),

        exportAttendance: async (params = {}) => {
            try {
                const queryString = new URLSearchParams(params).toString();
                const url = `/api/reports/export/attendance?${queryString}`;

                const token = getToken();
                const response = await fetch(url, {
                    method: 'GET',
                    headers: {'Authorization': `Bearer ${token}`}
                });

                if (!response.ok) throw new Error('Export failed');

                const blob = await response.blob();
                const downloadUrl = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = downloadUrl;
                a.download = `Davomat_${params.start_date}_${params.end_date}.xlsx`;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(downloadUrl);
                document.body.removeChild(a);

                return true;
            } catch (error) {
                console.error('Export error:', error);
                throw error;
            }
        }
    }
};

// Export for use in other files
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { API, getToken, setToken, removeToken };
}