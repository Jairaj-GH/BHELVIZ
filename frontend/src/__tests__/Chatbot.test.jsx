import { render, screen } from '@testing-library/react'
import Chatbot from '../Chatbot'
import React from 'react'

test('renders chat toggle button', () => {
  render(<Chatbot token={null} notify={() => {}} />)
  const btn = screen.getByRole('button', { name: /open chat/i }) || screen.queryByText('💬')
  expect(btn).toBeTruthy()
})
