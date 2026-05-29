import { useMutation, useQuery } from '@tanstack/react-query'
import { apiClient, toApiError } from './client'
import { useAuthStore } from '../store/authStore'
import type { User } from '../types/config'

export function useLogin() {
  const login = useAuthStore((s) => s.login)
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      login(email, password),
    onError: toApiError,
  })
}

export function useRegister() {
  const register = useAuthStore((s) => s.register)
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      register(email, password),
    onError: toApiError,
  })
}

export function useCurrentUser() {
  return useQuery({
    queryKey: ['currentUser'],
    queryFn: () => apiClient.get<User>('/auth/me').then((r) => r.data),
    enabled: false,
  })
}
