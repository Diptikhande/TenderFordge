import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
});

export interface Tender {
  id?: string;
  tender_id: string;
  filename: string;
  status: string;
  discovered_at?: string;
  document_size_bytes?: number;
}

export interface HealthResponse {
  status: string;
  service: string;
}

export const apiService = {
  async checkHealth(): Promise<HealthResponse> {
    const response = await apiClient.get<HealthResponse>('/health');
    return response.data;
  },

  async getTenders(): Promise<Tender[]> {
    const response = await apiClient.get<Tender[]>('/tenders/');
    return response.data;
  },

  async getTender(tenderId: string): Promise<Tender> {
    const response = await apiClient.get<Tender>(`/tenders/${tenderId}`);
    return response.data;
  },
};

export default apiClient;
