import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { apiClient } from '../api/client'
import type { User } from '../types/config'

interface AuthState {
  user: User | null
  token: string | null
  isInitializing: boolean
  isAuthenticated: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => void
  initialize: () => Promise<void>
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      token: null,
      isInitializing: true,
      isAuthenticated: false,

      login: async (email, password) => {
        const { data } = await apiClient.post<{ access_token: string }>('/auth/login', { email, password })
        apiClient.defaults.headers.common['Authorization'] = `Bearer ${data.access_token}`
        const me = await apiClient.get<User>('/auth/me')
        set({ token: data.access_token, user: me.data, isAuthenticated: true })
      },

      register: async (email, password) => {
        const { data } = await apiClient.post<{ access_token: string }>('/auth/register', { email, password })
        apiClient.defaults.headers.common['Authorization'] = `Bearer ${data.access_token}`
        const me = await apiClient.get<User>('/auth/me')
        set({ token: data.access_token, user: me.data, isAuthenticated: true })
      },

      logout: () => {
        delete apiClient.defaults.headers.common['Authorization']
        set({ user: null, token: null, isAuthenticated: false })
      },

      initialize: async () => {
        const { token } = get()
        if (!token) {
          set({ isInitializing: false })
          return
        }
        apiClient.defaults.headers.common['Authorization'] = `Bearer ${token}`
        try {
          const me = await apiClient.get<User>('/auth/me')
          set({ user: me.data, isAuthenticated: true, isInitializing: false })
        } catch {
          set({ user: null, token: null, isAuthenticated: false, isInitializing: false })
        }
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({ token: state.token, user: state.user }),
    },
  ),
)
