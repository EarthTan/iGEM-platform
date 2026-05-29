import axios, { AxiosError } from 'axios'
import { useAuthStore } from '../store/authStore'

export const apiClient = axios.create({ baseURL: '/api' })

export interface ApiError {
  type: 'NETWORK_ERROR' | 'UNAUTHORIZED' | 'FORBIDDEN' | 'NOT_FOUND' | 'VALIDATION_ERROR' | 'SERVER_ERROR'
  message: string
  status_code: number
  details?: Record<string, string[]>
}

export function toApiError(error: unknown): ApiError {
  if (!(error instanceof AxiosError) || !error.response) {
    return { type: 'NETWORK_ERROR', message: 'Network error', status_code: 0 }
  }
  const { status, data } = error.response
  const message: string = data?.detail || data?.message || 'An error occurred'
  switch (status) {
    case 401: return { type: 'UNAUTHORIZED', message, status_code: 401 }
    case 403: return { type: 'FORBIDDEN', message, status_code: 403 }
    case 404: return { type: 'NOT_FOUND', message, status_code: 404 }
    case 422: return { type: 'VALIDATION_ERROR', message, status_code: 422, details: data?.detail }
    default: return { type: 'SERVER_ERROR', message, status_code: status }
  }
}

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      useAuthStore.getState().logout()
    }
    return Promise.reject(error)
  },
)
