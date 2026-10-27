import request from './request'

export const login = (data) => request.post('/auth/token/', data)
export const refreshToken = (data) => request.post('/auth/token/refresh/', data)
export const getMe = () => request.get('/auth/me/')
export const updateMe = (data) => request.patch('/auth/me/', data)
export const changePassword = (data) => request.post('/auth/me/change_password/', data)
export const getUsers = () => request.get('/auth/users/')
